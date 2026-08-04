.PHONY: build-docker run-docker format format-check

build-docker:
	docker build -t dicomhawk:latest .

run-docker:
	docker run --rm -it -p 104:104 -p 11112:11112 --network=bridge --name dicomhawk dicomhawk:latest

# python3 -m resolves black from the active environment, where pip install -e .[dev] puts it.
format:
	python3 -m black .

format-check:
	python3 -m black --check .
