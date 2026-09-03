.PHONY: install seed run demo-reset test
install:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt
seed:
	.venv/bin/python -m app.seed
run:
	.venv/bin/uvicorn app.main:app --reload --port 8000
demo-reset:
	curl -s -X POST http://localhost:8000/api/demo/reset | python -m json.tool
