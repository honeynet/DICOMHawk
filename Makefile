.PHONY: docker
build-docker:
	docker build -t dicomhawk:latest .

run-docker:
	docker run --rm -it -p 104:104 -p 11112:11112 --network=bridge --name dicomhawk dicomhawk:latest

format:
	black .