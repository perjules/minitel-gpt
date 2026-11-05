# Producer captures

Generated with the actual Airzone `develop` diagnostics v1 on 5 November 2026:

```
python3 diagnostics.py state --mock > state-mock-v1.json
python3 diagnostics.py state > missing-gateway-v1.json
```

The latter exits 2. CLI implementation: `3408d6398bb3cdd6ae9f5e4988e6cd5679e74a58`;
runbook: `35d1bf23f13d029445adbcb6e6a33eef06581e07`.
Airzone `develop` inspected at `c6ea8d5eb8508286229a83a74e4820313e02cafc`.
The six zones are synthetic, not evidence of physical compatibility.
Tests refresh observation timestamps for replay. Any non-demo variant in tests
is synthetic input, never an on-site capture. No Airzone adapter code is copied.
