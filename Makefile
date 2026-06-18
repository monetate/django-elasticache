.PHONY: test install install-test test-tox dist release clean

test:
	pytest

install:
	pip install -e .

install-test:
	pip install -e '.[test]'

# test-tox runs the suite on every supported interpreter (2.7 and 3.7); Jenkins CI invokes this target.
test-tox:
	tox

dist: clean
	python setup.py sdist
	python setup.py bdist_wheel --universal
	ls -l dist

# release uploads the built artifacts to the `local` index from ~/.pypirc; Jenkins runs it on a VERSION bump.
release: dist
	twine upload dist/* -r local

clean:
	rm -rf .pytest_cache .tox build dist *.egg-info
	find . -name '*.py[co]' -delete
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
