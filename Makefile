build:
	docker build -t sow-screening .

run:
	docker run -it --env-file .env sow-screening

test:
	docker run --rm --env-file .env sow-screening pytest

# Quick local run (no Docker, for development)
dev:
	python3 main.py
