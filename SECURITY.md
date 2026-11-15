# Security policy

## Reporting a vulnerability

Report a vulnerability privately through GitHub: on the repository's **Security** tab, select **Report a
vulnerability**. This is the only way to report one; there is no e-mail contact. Do not report a vulnerability in a
public issue, a discussion or a pull request.

A report states:

- the part and the versions affected (GlucoCraft, the dashboard file or the panel configuration, and Home Assistant);
- what an attacker can do, and under which conditions;
- the steps to reproduce it.

Do not include real glucose data, health information, tokens, passwords, wall-panel addresses or network addresses; use
synthetic data and placeholders.

## Scope

In scope:

- the integration, `custom_components/glucocraft/` (its HTTP views, the wall-panel address, the stored files, the
  diagnostics and the logs);
- the dashboard files, `custom_components/glucocraft/www/`;
- the example panel configurations and the files they include, `esphome/`.

Not in scope: Home Assistant, ESPHome, HACS, the integrations that provide the glucose readings, and the security of
the installation and its network, which is the user's responsibility.

## Supported versions

Security fixes are made in the latest version.
