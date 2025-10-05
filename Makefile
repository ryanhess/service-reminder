.PHONY: make clean

make:
	python main.py

clean:
	python DB_Builder.py
	$(MAKE) make

