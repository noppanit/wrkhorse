PYTHON ?= python3

.PHONY: test demo demo-inference run

test:
	$(PYTHON) -m unittest discover -s tests -v

demo run:
	$(PYTHON) -m wrkhorse demo

demo-inference:
	$(PYTHON) -m wrkhorse demo --inference
