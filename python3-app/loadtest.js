import http from "k6/http";
import { check, sleep } from "k6";
import { randomIntBetween, randomItem } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8888";
const READ_RATIO = parseFloat(__ENV.READ_RATIO || "0.15"); // доля GET в итерациях

export const options = {
  stages: [
    { duration: "1m", target: 10 },
    { duration: "3m", target: 50 },
    { duration: "5m", target: 50 },
    { duration: "1m", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.01"],          // error rate < 1%
    http_req_duration: ["p(95)<300"],        // p95 latency < 300ms (пример)
  },
};

function toForm(data) {
  const parts = [];
  for (const k in data) {
    parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(data[k]));
  }
  return parts.join("&");
}

function postForm(path, data, tags = {}) {
  return http.post(`${BASE_URL}${path}`, toForm(data), {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    tags,
  });
}

function extractId(body) {
  // ожидаем: "OK: ID 1 for ..."
  const m = body.match(/OK:\s*ID\s*(\d+)/i);
  return m ? m[1] : null;
}

export function setup() {
  // 1) Создаём базовую больницу и доктора, чтобы в основной нагрузке
  // всегда был валидный doctor_ID и hospital_ID
  const hosp = postForm(
    "/hospital",
    {
      name: "LoadTest Hospital",
      address: "Test street",
      beds_number: "100",
      phone: "+100000",
    },
    { name: "POST /hospital (setup)" }
  );

  if (hosp.status !== 200) {
    throw new Error(`Setup failed: hospital status=${hosp.status}, body=${hosp.body}`);
  }
  const hospitalId = extractId(hosp.body);
  if (!hospitalId) throw new Error(`Cannot parse hospital id from: ${hosp.body}`);

  const doc = postForm(
    "/doctor",
    { surname: "LoadDoc", profession: "Therapist", hospital_ID: hospitalId },
    { name: "POST /doctor (setup)" }
  );

  if (doc.status !== 200) {
    throw new Error(`Setup failed: doctor status=${doc.status}, body=${doc.body}`);
  }
  const doctorId = extractId(doc.body);
  if (!doctorId) throw new Error(`Cannot parse doctor id from: ${doc.body}`);

  return { hospitalId, doctorId };
}

export default function (data) {
  // 1) POST /patient
  const sex = Math.random() < 0.5 ? "M" : "F";
  const patientResp = postForm(
    "/patient",
    {
      surname: `User${__VU}_${__ITER}`,
      born_date: "1990-01-01",
      sex: sex,
      mpn: `mpn-${__VU}-${__ITER}`,
    },
    { name: "POST /patient" }
  );

  const okPatient = check(patientResp, {
    "patient created (200)": (r) => r.status === 200,
  });

  const patientId = okPatient ? extractId(patientResp.body) : null;

  // Если пациент не создался — дальше нет смысла делать workflow
  if (!patientId) {
    sleep(0.2);
    return;
  }

  // 2) POST /diagnosis
  const diagResp = postForm(
    "/diagnosis",
    {
      patient_ID: patientId,
      type: randomItem(["flu", "cold", "covid", "injury"]),
      information: "loadtest",
    },
    { name: "POST /diagnosis" }
  );

  check(diagResp, {
    "diagnosis created (200)": (r) => r.status === 200,
  });

  // 3) POST /doctor-patient
  const linkResp = postForm(
    "/doctor-patient",
    { doctor_ID: data.doctorId, patient_ID: patientId },
    { name: "POST /doctor-patient" }
  );

  check(linkResp, {
    "doctor-patient linked (200)": (r) => r.status === 200,
  });

  // 4) Иногда делаем чтение (учтите: GET /patient со временем будет замедляться, т.к. O(N))
  if (Math.random() < READ_RATIO) {
    const readWhich = Math.random();
    if (readWhich < 0.5) {
      const r1 = http.get(`${BASE_URL}/patient`, { tags: { name: "GET /patient" } });
      check(r1, { "GET /patient 200": (r) => r.status === 200 });
    } else {
      const r2 = http.get(`${BASE_URL}/doctor-patient`, { tags: { name: "GET /doctor-patient" } });
      check(r2, { "GET /doctor-patient 200": (r) => r.status === 200 });
    }
  }

  sleep(randomIntBetween(0, 2) * 0.1);
}