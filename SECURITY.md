# Security and production limitations

ReconPilot is a local educational demonstration using fake payment records. It does not authenticate analysts, authorize users, connect to a financial institution, process funds, or represent regulatory compliance.

**Do not expose the application to the public Internet without additional protection.** The import and review endpoints allow state changes, and there is no identity provider, authorization, or CSRF strategy for a live multi-user deployment. The audit log is maintained in SQLite and can be modified by anyone with database access. The risk levels are deterministic business rules, not fraud prediction or an AML system.

The demonstration includes bounded CSV size and row count, input validation, parameterized SQL, unique settlement keys, cross-checks for existing payment IDs, conflict rejection, and spreadsheet formula-injection protection in exports. Those precautions do not make the application production-ready.

Before adapting this design for real financial records, implement: authenticated user and service identities; role-based access control; structured audit event retention with tamper-evident storage; encryption in transit/at rest and managed secrets; rate limits; secure headers and CSRF considerations; transaction-level authorization; durable background tasks; PostgreSQL with migrations and backups; scoped data retention and access logging; observability and incident response; privacy compliance; and independent security review.

Please report security concerns privately to the repository owner rather than posting exploitable details in a public issue.
