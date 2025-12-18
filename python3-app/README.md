# Hospital Demo (Tornado + Redis)

Учебный web-проект на Tornado, который хранит данные о больницах, врачах, пациентах, диагнозах и связях врач–пациент в Redis.

## Стек
- Python 3.10+ (рекомендуется)
- Tornado
- Redis
- HTML templates (Tornado templates)
- (для тестов) pytest, fakeredis
- (для нагрузочного тестирования) k6 (+ опционально InfluxDB/Grafana)

---

## Архитектура

### Компоненты
1. **Tornado Web Server**
   - Роутинг на `RequestHandler` классы
   - GET рендерит HTML шаблоны из `templates/`
   - POST принимает `application/x-www-form-urlencoded`

2. **Redis**
   - Основное хранилище
   - Сущности хранятся в `HASH`
   - Связь врач–пациент хранится в `SET`

### Схема данных в Redis

#### Hospital
- `hospital:autoID` — автоинкремент для ID
- `hospital:<id>` (HASH):
  - `name`
  - `address`
  - `phone`
  - `beds_number`

#### Doctor
- `doctor:autoID`
- `doctor:<id>` (HASH):
  - `surname`
  - `profession`
  - `hospital_ID` (может быть пустым)

#### Patient
- `patient:autoID`
- `patient:<id>` (HASH):
  - `surname`
  - `born_date`
  - `sex` (`M`/`F`)
  - `mpn`

#### Diagnosis
- `diagnosis:autoID`
- `diagnosis:<id>` (HASH):
  - `patient_ID`
  - `type`
  - `information`

#### Doctor ↔ Patient (many-to-many / one-to-many)
- `doctor-patient:<doctor_id>` (SET) — множество `patient_ID`

---

## Запуск

### Требования
- Redis (локально или в Docker)
- Python 3

### Установка зависимостей
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt



