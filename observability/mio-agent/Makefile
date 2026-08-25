# MIO Agent — Development commands

.PHONY: install test lint typecheck clean all

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install "pydantic>=2.9,<3" PyYAML==6.0.2 boto3==1.35.0 botocore==1.35.0
	.venv/bin/pip install pytest==8.3.3 pytest-cov==6.0.0 "moto[cloudwatch,logs,lambda,ec2,rds,ssm,sts,s3,dynamodb]>=5.0" ruff==0.7.4

test:
	PYTHONPATH=src .venv/bin/pytest tests/unit/ -v --tb=short \
		--cov=src/mio_agent \
		--cov-report=term-missing \
		--cov-report=xml:coverage.xml

lint:
	.venv/bin/ruff check src/ tests/

typecheck:
	.venv/bin/mypy src/ --ignore-missing-imports

clean:
	rm -rf .venv __pycache__ .pytest_cache .coverage coverage.xml htmlcov

all: install lint test
