.PHONY: make clean

install:
	pip install -r requirements.txt

test:
	pytest tests.py

make:
	python main.py

clean:
	$(MAKE) install
	python DB_Builder.py
	$(MAKE) make

