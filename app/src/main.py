import random
import time
import logging
from fastapi import FastAPI, Response
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Victim Service")

REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)
REQUEST_DURATION = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration',
    ['endpoint']
)
ERROR_RATE = Gauge('current_error_rate', 'Current error rate percentage')
ACTIVE_CHAOS = Gauge('chaos_mode_active', 'Whether chaos mode is active')

chaos_mode = False
error_probability = 0.1


@app.get("/health")
def health_check():
    return {"status": "ok", "chaos_mode": chaos_mode}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/data")
def get_data():
    start = time.time()
    try:
        if random.random() < 0.3:
            delay = random.uniform(0.5, 3.0)
            logger.warning(f"Slow response, adding {delay:.2f}s delay")
            time.sleep(delay)

        current_error_prob = 0.8 if chaos_mode else error_probability

        if random.random() < current_error_prob:
            logger.error("Internal error occurred")
            REQUEST_COUNT.labels(method='GET', endpoint='/api/data', status='500').inc()
            return Response(
                content='{"error": "Internal Server Error"}',
                status_code=500,
                media_type="application/json"
            )

        logger.info("Successfully processed request")
        REQUEST_COUNT.labels(method='GET', endpoint='/api/data', status='200').inc()
        return {"data": [random.randint(1, 100) for _ in range(10)], "chaos_mode": chaos_mode}

    finally:
        duration = time.time() - start
        REQUEST_DURATION.labels(endpoint='/api/data').observe(duration)


@app.post("/chaos/enable")
def enable_chaos():
    global chaos_mode
    chaos_mode = True
    ACTIVE_CHAOS.set(1)
    ERROR_RATE.set(80)
    logger.critical("CHAOS MODE ENABLED!")
    return {"chaos_mode": True, "message": "Chaos enabled!"}


@app.post("/chaos/disable")
def disable_chaos():
    global chaos_mode
    chaos_mode = False
    ACTIVE_CHAOS.set(0)
    ERROR_RATE.set(error_probability * 100)
    logger.info("Chaos mode disabled")
    return {"chaos_mode": False, "message": "Chaos disabled"}


@app.get("/")
def root():
    return {"service": "victim-service", "version": "1.0.0"}
