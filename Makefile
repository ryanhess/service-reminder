.PHONY: install run

run:
	python main.py

install:
	pip install -r requirements.txt

test:
	pytest tests.py

clean:
	python DB_Builder.py
	$(Make) run