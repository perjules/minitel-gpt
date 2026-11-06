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

`dashboard-addresses.json` is the actual return value from Airzone
`network.dashboard_addresses('https://pi.family.ts.net:8443', 8081)` at develop
`684b008fe6fabce945419c8803c79315ac98256b`, generated 6 November. Its interface
enumeration was replaced with the synthetic address `192.168.1.42`; no real
network interfaces/gateway were probed. `/api/addresses` JSON has no schema-version
field. Consumer tests use the producer's fields unchanged: `origin`, `loopback`,
`lan_enabled`, `lan_urls`. This fixture establishes serialization compatibility,
not physical LAN/HVAC acceptance. The Airzone repository is unchanged.
