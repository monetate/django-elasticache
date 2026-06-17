.PHONY: test install tox clean

test:
	pytest

install:
	pip install -e '.[test]'

# tox runs the suite on every supported interpreter (2.7 and 3.7).
tox:
	tox

clean:
	rm -rf .pytest_cache .tox build dist *.egg-info
	find . -name '*.py[co]' -delete
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
