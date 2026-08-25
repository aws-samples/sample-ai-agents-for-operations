# Contributing to aws-mio-agent

Thank you for your interest in contributing to this project! We welcome contributions via pull requests, bug reports, feature requests, and documentation improvements.

## How to Contribute

### Reporting Bugs

1. Check the [existing issues](../../issues) to avoid duplicates.
2. Open a new issue with a clear title and description.
3. Include steps to reproduce, expected behavior, and actual behavior.
4. Include relevant logs and error messages.

### Suggesting Features

1. Open a new issue with the label `enhancement`.
2. Describe the problem you're solving and your proposed solution.
3. Explain why this feature would be useful to others.

### Submitting Pull Requests

1. **Fork** the repository and create a new branch from `main`.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/aws-mio-agent.git
   cd aws-mio-agent
   git checkout -b feature/my-new-feature
   ```
3. **Set up the development environment**:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```
4. **Make your changes** following the code style guidelines below.
5. **Run tests** to ensure nothing is broken:
   ```bash
   pytest tests/unit/ --cov=src/mio_agent
   ```
6. **Run linting and type checks**:
   ```bash
   ruff check src/ tests/
   mypy src/
   ```
7. **Commit** your changes with a descriptive message:
   ```bash
   git commit -m "feat: add support for EKS Container Insights detection"
   ```
8. **Push** your branch and open a pull request.

## Code Style Guidelines

- Python 3.12+ with type annotations on all public functions
- Line length: 120 characters (enforced by ruff)
- Use `ruff` for linting (`ruff check src/ tests/`)
- Use `mypy` for type checking (`mypy src/`)
- Follow [PEP 8](https://peps.python.org/pep-0008/) naming conventions
- Write docstrings for all public classes and functions (Google style)
- No hardcoded credentials, account IDs, or personally identifiable information

## Testing Requirements

- All new features must include unit tests
- Maintain test coverage ≥ 80%
- Use `moto` for mocking AWS API calls — do not make real AWS API calls in tests
- Test files follow the pattern `tests/unit/test_<module>.py`

## Security

Do not include AWS credentials, account IDs, or any sensitive information in your code, comments, or commit messages. See [SECURITY.md](SECURITY.md) if you discover a security vulnerability.

## Code of Conduct

This project follows the [Amazon Open Source Code of Conduct](CODE_OF_CONDUCT.md). Please review it before contributing.

## Licensing

By submitting a pull request, you agree that your contribution is licensed under the [MIT-0 License](LICENSE).
