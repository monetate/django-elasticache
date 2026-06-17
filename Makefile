.PHONY: test install install-test test-tox clean

test:
	pytest

install:
	pip install -e .

install-test:
	pip install -e '.[test]'

# test-tox runs the suite on every supported interpreter (2.7 and 3.7); Jenkins CI invokes this target.
test-tox:
	tox

clean:
	rm -rf .pytest_cache .tox build dist *.egg-info
	find . -name '*.py[co]' -delete
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
