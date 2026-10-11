# Contributing

Work locally, keep changes focused, and test in Home Assistant before opening a
pull request.

- Use clear commit messages.
- Do not commit Home Assistant tokens, camera proxy tokens, SSH keys, or local
  deployment notes.
- Keep the integration domain as `elegoo_spaghetti_detection`.
- Validate JSON, YAML, Python syntax, HACS, and Hassfest before opening a pull
  request.
- Run the tests and add tests for new behaviour.

## Local Validation

```bash
python -m json.tool hacs.json > /dev/null
python -m json.tool custom_components/elegoo_spaghetti_detection/manifest.json > /dev/null
python -m compileall custom_components/elegoo_spaghetti_detection addon/rootfs/app
```

## Tests

The tests use `pytest` with
[`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component),
which pins the Home Assistant version they run against. With
[uv](https://docs.astral.sh/uv/) (Python 3.14):

```bash
uv sync
uv run pytest
```

The GitHub workflows run the full repository checks, including the tests.
