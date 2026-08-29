.PHONY: setup run collector test docker clean-runtime
setup:
	./scripts/setup-p0.sh
run:
	./scripts/run-p0.sh
collector:
	./scripts/run-collector.sh
test:
	./scripts/test-p0.sh
docker:
	docker compose up --build
clean-runtime:
	rm -rf data/runtime reports
	mkdir -p data/runtime reports
