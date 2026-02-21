.PHONY: docker
build-docker:
	docker build -t dicomhawk:latest .

run-docker:
	docker run --rm -it -p 11112:11112 --name dicomhawk dicomhawk:latest

format:
	black .