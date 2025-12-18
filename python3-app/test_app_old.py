import os
import tempfile
import urllib.parse

import fakeredis
import redis
import tornado.web
from tornado.testing import AsyncHTTPTestCase

import main as myapp  # <-- если ваш файл называется иначе, поменяйте здесь


def _write_templates(root_dir: str):
    """
    Ваш код рендерит 'templates/xxx.html'.
    Поэтому делаем template_path=root_dir, а файлы кладём в root_dir/templates/.
    """
    tpl_dir = os.path.join(root_dir, "templates")
    os.makedirs(tpl_dir, exist_ok=True)

    templates = {
        "index.html": "<html>index</html>",
        "hospital.html": "<html>hospital {{ items }}</html>",
        "doctor.html": "<html>doctor {{ items }}</html>",
        "patient.html": "<html>patient {{ items }}</html>",
        "diagnosis.html": "<html>diagnosis {{ items }}</html>",
        "doctor-patient.html": "<html>doctor-patient {{ items }}</html>",
    }

    for name, content in templates.items():
        with open(os.path.join(tpl_dir, name), "w", encoding="utf-8") as f:
            f.write(content)


class TestApp(AsyncHTTPTestCase):
    def setUp(self):
        # подменяем redis на fakeredis на каждый тест
        self.fake_redis = fakeredis.FakeStrictRedis()

        # В вашем модуле redis-клиент создан глобально: myapp.r
        myapp.r = self.fake_redis

        # сбросим БД + инициализация autoID
        myapp.r.flushall()
        myapp.init_db()

        super().setUp()

    def get_app(self):
        # временная директория для шаблонов
        self._tmpdir = tempfile.TemporaryDirectory()
        _write_templates(self._tmpdir.name)

        return tornado.web.Application(
            [
                (r"/", myapp.MainHandler),
                (r"/hospital", myapp.HospitalHandler),
                (r"/doctor", myapp.DoctorHandler),
                (r"/patient", myapp.PatientHandler),
                (r"/diagnosis", myapp.DiagnosisHandler),
                (r"/doctor-patient", myapp.DoctorPatientHandler),
            ],
            template_path=self._tmpdir.name,  # важно
            autoreload=False,
            debug=False,
            compiled_template_cache=False,
        )

    def tearDown(self):
        super().tearDown()
        self._tmpdir.cleanup()

    # ---------- helpers ----------
    def _post_form(self, url: str, data: dict):
        body = urllib.parse.urlencode(data)
        return self.fetch(
            url,
            method="POST",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    # ---------- tests ----------
    def test_get_main_page_200(self):
        resp = self.fetch("/")
        assert resp.code == 200
        assert b"index" in resp.body

    def test_hospital_post_requires_name_and_address(self):
        resp = self._post_form("/hospital", {
            "name": "",
            "address": "",
            "beds_number": "",
            "phone": ""
        })
        assert resp.code == 400
        assert b"Hospital name and address required" in resp.body

    def test_hospital_post_ok_and_stored(self):
        resp = self._post_form(
            "/hospital",
            {
                "name": "City Hospital",
                "address": "Main st 1",
                "beds_number": "100",
                "phone": "+123",
            },
        )
        assert resp.code == 200
        body = resp.body.decode("utf-8")
        assert "OK: ID 1 for City Hospital" in body

        h = myapp.r.hgetall("hospital:1")
        assert h[b"name"] == b"City Hospital"
        assert h[b"address"] == b"Main st 1"
        assert h[b"beds_number"] == b"100"
        assert h[b"phone"] == b"+123"

    def test_doctor_post_requires_existing_hospital_if_hospital_id_given(self):
        # hospital_ID указан, но hospital:999 не существует
        resp = self._post_form(
            "/doctor",
            {"surname": "Ivanov", "profession": "Surgeon", "hospital_ID": "999"},
        )
        assert resp.code == 400
        assert b"No hospital with such ID" in resp.body

    def test_doctor_post_ok(self):
        # Сначала создаём hospital:1
        self._post_form(
            "/hospital",
            {"name": "H1", "address": "A1", "beds_number": "10", "phone": "1"},
        )

        resp = self._post_form(
            "/doctor",
            {"surname": "Petrov", "profession": "Therapist", "hospital_ID": "1"},
        )
        assert resp.code == 200
        assert b"OK: ID 1 for Petrov" in resp.body

        d = myapp.r.hgetall("doctor:1")
        assert d[b"surname"] == b"Petrov"
        assert d[b"profession"] == b"Therapist"
        assert d[b"hospital_ID"] == b"1"

    def test_patient_post_sex_validation(self):
        resp = self._post_form(
            "/patient",
            {"surname": "Sidorov", "born_date": "2000-01-01", "sex": "X", "mpn": "abc"},
        )
        assert resp.code == 400
        assert b"Sex must be 'M' or 'F'" in resp.body

    def test_patient_post_ok(self):
        resp = self._post_form(
            "/patient",
            {"surname": "Sidorov", "born_date": "2000-01-01", "sex": "M", "mpn": "abc"},
        )
        assert resp.code == 200
        assert b"OK: ID 1 for Sidorov" in resp.body

        p = myapp.r.hgetall("patient:1")
        assert p[b"surname"] == b"Sidorov"
        assert p[b"born_date"] == b"2000-01-01"
        assert p[b"sex"] == b"M"
        assert p[b"mpn"] == b"abc"

    def test_diagnosis_requires_existing_patient(self):
        resp = self._post_form(
            "/diagnosis",
            {"patient_ID": "123", "type": "flu", "information": "test"},
        )
        assert resp.code == 400
        assert b"No patient with such ID" in resp.body

    def test_diagnosis_post_ok_includes_patient_surname(self):
        # пациент:1
        self._post_form(
            "/patient",
            {"surname": "Doe", "born_date": "1990-01-01", "sex": "F", "mpn": "mpn1"},
        )

        resp = self._post_form(
            "/diagnosis",
            {"patient_ID": "1", "type": "flu", "information": "mild"},
        )
        assert resp.code == 200
        assert b"OK: ID 1 for patient Doe" in resp.body

        dg = myapp.r.hgetall("diagnosis:1")
        assert dg[b"patient_ID"] == b"1"
        assert dg[b"type"] == b"flu"
        assert dg[b"information"] == b"mild"

    def test_doctor_patient_link_ok(self):
        # подготовка: doctor:1 и patient:1
        self._post_form(
            "/hospital",
            {"name": "H1", "address": "A1", "beds_number": "10", "phone": "1"},
        )
        self._post_form(
            "/doctor",
            {"surname": "House", "profession": "Diagnostician", "hospital_ID": "1"},
        )
        self._post_form(
            "/patient",
            {"surname": "Patient1", "born_date": "1980-01-01", "sex": "M", "mpn": "m1"},
        )

        resp = self._post_form("/doctor-patient", {"doctor_ID": "1", "patient_ID": "1"})
        assert resp.code == 200
        assert b"OK: doctor ID: 1, patient ID: 1" in resp.body

        s = myapp.r.smembers("doctor-patient:1")
        assert b"1" in s

    def test_redis_connection_error_returns_400(self):
        # искусственно ломаем redis на одном из вызовов
        def boom(*args, **kwargs):
            raise redis.exceptions.ConnectionError("nope")

        myapp.r.get = boom

        resp = self.fetch("/hospital")  # в get() вызывается r.get("hospital:autoID")
        assert resp.code == 400
        assert b"Redis connection refused" in resp.body