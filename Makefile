# StageProof Makefile (TASKS T002).
# NOTE: this host has no `make`; execute the underlying commands directly.

PYTHON ?= python

.PHONY: setup data fit scenarios test demo eval cache clean

setup:
	$(PYTHON) -m pip install -e .[data,dev]

data:
	$(PYTHON) scripts/prepare_data.py

fit:
	$(PYTHON) scripts/fit_models.py

scenarios:
	$(PYTHON) scripts/build_scenarios.py

test:
	$(PYTHON) -m pytest

demo:
	$(PYTHON) -m streamlit run dashboard/app.py

eval:
	$(PYTHON) scripts/evaluate.py

cache:
	$(PYTHON) scripts/run_scenario.py --scenario A --export
	$(PYTHON) scripts/run_scenario.py --scenario C --export
	$(PYTHON) scripts/run_scenario.py --scenario D --export
	$(PYTHON) scripts/run_scenario.py --scenario E --export
	$(PYTHON) scripts/run_scenario.py --scenario F --export

clean:
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]; shutil.rmtree('.pytest_cache', ignore_errors=True)"
