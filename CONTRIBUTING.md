# Contributing

Contributions are welcome, and they are greatly appreciated! Every little bit helps, and credit will always be given.

## Setting up development environment

This repository is using [devcontainers](https://code.visualstudio.com/docs/devcontainers/containers) to facilitate the development setup. Simply cloning the repository to your local machine, and opening the folder in Visual Studio Code should prompt you if you want to setup the devcontainer.

This method is highly recommended, as it will handle everything the whole setup process and allows you to get straight to developing! If you rather manually set upp your environment, follow the steps below:

### Manual setup

Set up a virtual environment to prevent dependencies from polluting your global python installation

```bash
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
# OR
.venv\Scripts\activate      # Windows PowerShell
```

Install packages once virtualenv is created and activated

```bash
pip install -e .[dev]
pre-commit install
```

## Tasks

Several commonly used tasks (linting, tests) are defined as Visual Studio Code tasks, allowing them to be run from inside VSCode. If for some reason you want to manually run any of the tasks, take a look at [the tasks configuration](.vscode/tasks.json) file to find the commands used.

## Pull Request Guidelines

Before you submit a pull request, check that it meets these guidelines, automated Github Actions will verify most of these items:

1. Pre-commit hooks should catch any formatting issues, formatting can be manually run using VSCode tasks.
2. Tests should pass, new functionality should ideally include new tests. Tests can be run using VSCode tasks.
