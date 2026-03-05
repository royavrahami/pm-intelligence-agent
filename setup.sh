#!/usr/bin/env bash
# PM Intelligence Agent – Linux/macOS Setup Script
set -euo pipefail

echo "Setting up PM Intelligence Agent..."

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example – please edit it and add your OPENAI_API_KEY"
fi

# Create required directories
mkdir -p data reports logs

echo ""
echo "Setup complete!"
echo "Next steps:"
echo "  1. Edit .env and set OPENAI_API_KEY=sk-..."
echo "  2. Run: python main.py run"
echo "  3. For recurring schedule: python main.py schedule"
