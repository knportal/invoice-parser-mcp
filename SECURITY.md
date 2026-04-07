# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | Yes       |
| < 1.0   | No        |

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not** open a public GitHub issue.

Instead, email us at **security@plenitudo.ai** with:

- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested remediation (optional)

We will acknowledge your report within **72 hours** and aim to release a fix within 14 days for critical issues.

## API Key Security

- **Never commit API keys** to version control. Add `.env` to your `.gitignore`.
- If a key is exposed, **rotate it immediately** by contacting [hello@plenitudo.ai](mailto:hello@plenitudo.ai).
- API keys should be treated as secrets regardless of tier.
- Keys are stored hashed in the database — we cannot retrieve your plaintext key after creation.

## Anthropic API Key Security

- The `ANTHROPIC_API_KEY` environment variable gives access to Claude Vision and should be kept secret.
- On Railway, set it as a private environment variable — never hardcode it in source files.
- If using `maxproxy` locally, set `ANTHROPIC_API_KEY=maxproxy` and `ANTHROPIC_BASE_URL=http://localhost:3456`. Do not set `ANTHROPIC_BASE_URL` in cloud deployments.

## x402 Payment Security

- x402 payment proofs (transaction hashes) are single-use. Each transaction hash is recorded in the database after first use and rejected on replay.
- The USDC wallet address is configured server-side. Do not send payments to addresses found in client-side code or documentation — always verify the address returned by the server's payment-required response.

## Responsible Disclosure

We follow responsible disclosure principles. Researchers who report valid vulnerabilities in good faith will be credited in the release notes (unless they prefer to remain anonymous).
