#!/usr/bin/env python3
import logging
import os
from typing import Dict, List, Any, Optional

import redis
import tornado.ioloop
import tornado.web
from tornado.options import parse_command_line

import time
from typing import Tuple


PORT = int(os.environ.get("PORT", "8888"))


def create_redis_client() -> redis.StrictRedis:
    return redis.StrictRedis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        db=0,
    )


# глобальный клиент оставлен для обратной совместимости (и тестов, которые monkeypatch-ят myapp.r)
r = create_redis_client()


# --- Redis helpers ---

_ALLOCATE_ID_LUA = """
local v = redis.call('GET', KEYS[1])
if not v then
  redis.call('SET', KEYS[1], '1')
  v = '1'
end
redis.call('INCR', KEYS[1])
return v
"""


def allocate_id(redis_client: redis.StrictRedis, key: str) -> str:
    """
    Атомарно:
      - если key отсутствует: set key=1
      - вернуть текущее значение
      - incr key
    Сохраняет старую семантику: первая сущность получает ID=1,
    потому что init_db выставляет autoID=1.
    """
    value = redis_client.eval(_ALLOCATE_ID_LUA, 1, key)
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def decode_hash(h: Dict[bytes, bytes]) -> Dict[str, str]:
    return {k.decode(): v.decode() for k, v in h.items()}


def list_entities(redis_client: redis.StrictRedis, prefix: str) -> List[Dict[str, str]]:
    """
    Возвращает список hash-сущностей вида prefix:<id>.
    Использует SCAN, затем сортировку по числовому id.
    """
    keys = []
    pattern = f"{prefix}:*"
    for k in redis_client.scan_iter(match=pattern, count=200):
        # пропускаем autoID ключи вроде "prefix:autoID" (они не совпадают с pattern ':*'? совпадают)
        # поэтому фильтруем: id должен быть цифрой
        key = k.decode() if isinstance(k, bytes) else str(k)
        parts = key.split(":")
        if len(parts) != 2:
            continue
        if parts[1].isdigit():
            keys.append(key)

    keys.sort(key=lambda s: int(s.split(":")[1]))

    items: List[Dict[str, str]] = []
    for key in keys:
        h = redis_client.hgetall(key)
        if h:
            items.append(decode_hash(h))
    return items


def save_hash_and_validate(
    redis_client: redis.StrictRedis,
    key: str,
    mapping: Dict[str, str],
    expected_fields: int,
) -> None:
    """
    Пишет hash и проверяет целостность через HLEN.
    """
    pipe = redis_client.pipeline()
    pipe.hset(key, mapping=mapping)
    pipe.hlen(key)
    _, hlen = pipe.execute()

    if int(hlen) != expected_fields:
        raise RuntimeError("hash validation failed")


def init_db(redis_client: Optional[redis.StrictRedis] = None) -> None:
    """
    Инициализация ключей. Поведение сохранено:
    - если db_initiated отсутствует, выставляем autoID=1
    """
    rc = redis_client or r
    db_initiated = rc.get("db_initiated")
    if not db_initiated:
        rc.set("hospital:autoID", 1)
        rc.set("doctor:autoID", 1)
        rc.set("patient:autoID", 1)
        rc.set("diagnosis:autoID", 1)
        rc.set("db_initiated", 1)


# --- Tornado Handlers ---

class BaseHandler(tornado.web.RequestHandler):
    @property
    def redis(self) -> redis.StrictRedis:
        # используем глобальный r для совместимости (и чтобы существующие тесты могли подменять myapp.r)
        return r

    def write_redis_refused(self):
        self.set_status(400)
        self.write("Redis connection refused")


class MainHandler(BaseHandler):
    def get(self):
        self.render("templates/index.html")


class HospitalHandler(BaseHandler):
    def get(self):
        try:
            items = list_entities(self.redis, "hospital")
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        else:
            self.render("templates/hospital.html", items=items)

    def post(self):
        name = self.get_argument("name", default="")
        address = self.get_argument("address", default="")
        beds_number = self.get_argument("beds_number", default="")
        phone = self.get_argument("phone", default="")

        if not name or not address:
            self.set_status(400)
            self.write("Hospital name and address required")
            return

        logging.debug("%s %s %s %s", name, address, beds_number, phone)

        try:
            ID = allocate_id(self.redis, "hospital:autoID")
            key = f"hospital:{ID}"

            save_hash_and_validate(
                self.redis,
                key,
                {
                    "name": name,
                    "address": address,
                    "phone": phone,
                    "beds_number": beds_number,
                },
                expected_fields=4,
            )
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        except RuntimeError:
            self.set_status(500)
            self.write("Something went terribly wrong")
        else:
            self.write("OK: ID " + ID + " for " + name)


class DoctorHandler(BaseHandler):
    def get(self):
        try:
            items = list_entities(self.redis, "doctor")
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        else:
            self.render("templates/doctor.html", items=items)

    def post(self):
        surname = self.get_argument("surname", default="")
        profession = self.get_argument("profession", default="")
        hospital_ID = self.get_argument("hospital_ID", default="")

        if not surname or not profession:
            self.set_status(400)
            self.write("Surname and profession required")
            return

        logging.debug("%s %s", surname, profession)

        try:
            if hospital_ID:
                hospital = self.redis.hgetall("hospital:" + hospital_ID)
                if not hospital:
                    self.set_status(400)
                    self.write("No hospital with such ID")
                    return

            ID = allocate_id(self.redis, "doctor:autoID")
            key = f"doctor:{ID}"

            save_hash_and_validate(
                self.redis,
                key,
                {
                    "surname": surname,
                    "profession": profession,
                    "hospital_ID": hospital_ID,
                },
                expected_fields=3,
            )
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        except RuntimeError:
            self.set_status(500)
            self.write("Something went terribly wrong")
        else:
            self.write("OK: ID " + ID + " for " + surname)


class PatientHandler(BaseHandler):
    def get(self):
        try:
            items = list_entities(self.redis, "patient")
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        else:
            self.render("templates/patient.html", items=items)

    def post(self):
        surname = self.get_argument("surname", default="")
        born_date = self.get_argument("born_date", default="")
        sex = self.get_argument("sex", default="")
        mpn = self.get_argument("mpn", default="")

        if not surname or not born_date or not sex or not mpn:
            self.set_status(400)
            self.write("All fields required")
            return

        if sex not in ["M", "F"]:
            self.set_status(400)
            self.write("Sex must be 'M' or 'F'")
            return

        logging.debug("%s %s %s %s", surname, born_date, sex, mpn)

        try:
            ID = allocate_id(self.redis, "patient:autoID")
            key = f"patient:{ID}"

            save_hash_and_validate(
                self.redis,
                key,
                {
                    "surname": surname,
                    "born_date": born_date,
                    "sex": sex,
                    "mpn": mpn,
                },
                expected_fields=4,
            )
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        except RuntimeError:
            self.set_status(500)
            self.write("Something went terribly wrong")
        else:
            self.write("OK: ID " + ID + " for " + surname)


class DiagnosisHandler(BaseHandler):
    def get(self):
        try:
            items = list_entities(self.redis, "diagnosis")
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        else:
            self.render("templates/diagnosis.html", items=items)

    def post(self):
        patient_ID = self.get_argument("patient_ID", default="")
        diagnosis_type = self.get_argument("type", default="")
        information = self.get_argument("information", default="")

        if not patient_ID or not diagnosis_type:
            self.set_status(400)
            self.write("Patiend ID and diagnosis type required")
            return

        logging.debug("%s %s %s", patient_ID, diagnosis_type, information)

        try:
            patient = self.redis.hgetall("patient:" + patient_ID)
            if not patient:
                self.set_status(400)
                self.write("No patient with such ID")
                return

            ID = allocate_id(self.redis, "diagnosis:autoID")
            key = f"diagnosis:{ID}"

            save_hash_and_validate(
                self.redis,
                key,
                {
                    "patient_ID": patient_ID,
                    "type": diagnosis_type,
                    "information": information,
                },
                expected_fields=3,
            )
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        except RuntimeError:
            self.set_status(500)
            self.write("Something went terribly wrong")
        else:
            self.write("OK: ID " + ID + " for patient " + patient[b"surname"].decode())


class DoctorPatientHandler(BaseHandler):
    def get(self):
        items: Dict[str, List[str]] = {}
        try:
            # перечисляем doctor:* через scan вместо range(autoID)
            for k in self.redis.scan_iter(match="doctor:*", count=200):
                key = k.decode() if isinstance(k, bytes) else str(k)
                parts = key.split(":")
                if len(parts) != 2 or not parts[1].isdigit():
                    continue
                doctor_id = parts[1]

                s = self.redis.smembers("doctor-patient:" + doctor_id)
                if s:
                    items[doctor_id] = sorted(
                        [(x.decode() if isinstance(x, bytes) else str(x)) for x in s],
                        key=lambda v: int(v) if v.isdigit() else v,
                    )

        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        else:
            self.render("templates/doctor-patient.html", items=items)

    def post(self):
        doctor_ID = self.get_argument("doctor_ID", default="")
        patient_ID = self.get_argument("patient_ID", default="")
        if not doctor_ID or not patient_ID:
            self.set_status(400)
            self.write("ID required")
            return

        logging.debug("%s %s", doctor_ID, patient_ID)

        try:
            patient = self.redis.hgetall("patient:" + patient_ID)
            doctor = self.redis.hgetall("doctor:" + doctor_ID)

            if not patient or not doctor:
                self.set_status(400)
                self.write("No such ID for doctor or patient")
                return

            self.redis.sadd("doctor-patient:" + doctor_ID, patient_ID)

        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
        else:
            self.write("OK: doctor ID: " + doctor_ID + ", patient ID: " + patient_ID)

def count_entities(redis_client: redis.StrictRedis, prefix: str) -> int:
    """
    Считает количество ключей вида prefix:<id>, где <id> - число.
    prefix:autoID будет отфильтрован.
    """
    cnt = 0
    pattern = f"{prefix}:*"
    for k in redis_client.scan_iter(match=pattern, count=500):
        key = k.decode() if isinstance(k, bytes) else str(k)
        parts = key.split(":")
        if len(parts) != 2:
            continue
        if parts[1].isdigit():
            cnt += 1
    return cnt


def count_doctor_patient(redis_client: redis.StrictRedis) -> Tuple[int, int]:
    """
    Возвращает:
      - количество doctor-patient SET-ов (сколько докторов имеют хотя бы одну связь)
      - количество всех связей doctor->patient (сумма размеров SET-ов)
    """
    sets_cnt = 0
    links_cnt = 0
    for k in redis_client.scan_iter(match="doctor-patient:*", count=500):
        key = k.decode() if isinstance(k, bytes) else str(k)
        parts = key.split(":")
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        sets_cnt += 1
        links_cnt += int(redis_client.scard(key))
    return sets_cnt, links_cnt

class BaseHandler(tornado.web.RequestHandler):
    @property
    def redis(self) -> redis.StrictRedis:
        return r

    def write_redis_refused(self):
        self.set_status(400)
        self.write("Redis connection refused")

    def prepare(self):
        """
        Best-effort сбор статистики:
          - stats:start_ts: время первого запроса (unix sec)
          - stats:requests: общее число запросов
        Никогда не ломает ответ, даже если Redis недоступен.
        """
        try:
            now = int(time.time())
            pipe = self.redis.pipeline()
            pipe.setnx("stats:start_ts", now)
            pipe.incr("stats:requests")
            pipe.execute()
        except Exception:
            # статистика не должна влиять на бизнес-эндпоинты
            pass

class AnalyticsEntitiesHandler(BaseHandler):
    def get(self):
        try:
            hospitals = count_entities(self.redis, "hospital")
            doctors = count_entities(self.redis, "doctor")
            patients = count_entities(self.redis, "patient")
            diagnoses = count_entities(self.redis, "diagnosis")
            dp_sets, dp_links = count_doctor_patient(self.redis)
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
            return

        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.write({
            "hospitals": hospitals,
            "doctors": doctors,
            "patients": patients,
            "diagnoses": diagnoses,
            "doctor_patient_sets": dp_sets,
            "doctor_patient_links": dp_links,
        })


class AnalyticsUsageHandler(BaseHandler):
    def get(self):
        try:
            start_ts = self.redis.get("stats:start_ts")
            total = self.redis.get("stats:requests")
        except redis.exceptions.ConnectionError:
            self.write_redis_refused()
            return

        now = int(time.time())
        start_ts = int(start_ts.decode()) if start_ts else now
        total = int(total.decode()) if total else 0

        uptime = max(1, now - start_ts)
        avg_rps = total / uptime
        avg_rpm = avg_rps * 60

        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.write({
            "start_ts": start_ts,
            "now_ts": now,
            "uptime_seconds": uptime,
            "total_requests": total,
            "avg_rps": avg_rps,
            "avg_rpm": avg_rpm,
        })


def make_app() -> tornado.web.Application:
    return tornado.web.Application(
        [
            (r"/", MainHandler),
            (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": "static/"}),
            (r"/hospital", HospitalHandler),
            (r"/doctor", DoctorHandler),
            (r"/patient", PatientHandler),
            (r"/diagnosis", DiagnosisHandler),
            (r"/doctor-patient", DoctorPatientHandler),
            (r"/analytics/entities", AnalyticsEntitiesHandler),
            (r"/analytics/usage", AnalyticsUsageHandler),
        ],
        autoreload=True,
        debug=True,
        compiled_template_cache=False,
        serve_traceback=True,
    )

def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parse_command_line()

    init_db(r)

    app = make_app()
    app.listen(PORT)
    logging.info("Listening on %s", PORT)
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()