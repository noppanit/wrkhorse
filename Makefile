PYTHON ?= python3

.PHONY: test demo run

test:
	$(PYTHON) -m unittest discover -s tests -v

demo run:
	$(PYTHON) -m wrkhorse demo
