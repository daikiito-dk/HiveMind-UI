.PHONY: setup demo app test lint viral clean help

help:
	@echo "setup   Install dependencies + Chromium and create .env"
	@echo "demo    Run a 3-persona offline mock cohort on the retro bank sample"
	@echo "app     Launch the Streamlit dashboard"
	@echo "viral   Generate shareable replay GIFs + X post copy"
	@echo "test    Run the test suite (offline, no browser needed)"
	@echo "lint    Run ruff"
	@echo "clean   Remove run artifacts"

setup:
	uv sync
	uv run playwright install chromium
	@test -f .env || cp .env.example .env
	@echo "Done. Edit .env to add XAI_API_KEY for live Grok mode."

demo:
	uv run python cli.py run --url "file://$(CURDIR)/samples/retro_bank.html" \
		--task "残高を確認して、他行あての振込画面までたどり着く" \
		--context "今朝のニュースで物価高が報じられた直後。節約志向が強まっている。" \
		--personas 3 --mode mock --speed 3

viral:
	uv run python demo_tools/viral_demo.py

app:
	uv run streamlit run app.py

test:
	uv run pytest

lint:
	uv run ruff check .

clean:
	rm -rf runs
