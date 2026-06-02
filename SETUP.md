# Setup Instructions

## 1. Install Python

Download and install Python 3.11+ from https://www.python.org/downloads/
- During install: check "Add Python to PATH"
- Restart your terminal after installation

## 2. Install dependencies

Open a terminal in this folder and run:

```
pip install -r requirements.txt
```

## 3. (Optional) Configure AI research

```
copy .env.example .env
```

Open `.env` and add your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Without a key, the app uses rule-based analysis automatically.

## 4. Run

```
python main.py
```
