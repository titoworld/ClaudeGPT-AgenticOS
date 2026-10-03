<!-- @question -->
Server backup policy

Write our server's backup policy, on one page: what gets backed up, how often, where it's kept, for how long, and how we check that it can be restored. There are three of us and none of us is a full-time sysadmin.
<!-- @claude-answer -->
# Backup policy

## Scope

The `vps-01` server: the application, the PostgreSQL database and the files users upload.

## Backups

- **Database:** a full `pg_basebackup` every night at 02:00, and continuous archiving of the transaction log (WAL) every 15 minutes.
- **Files:** an encrypted incremental backup every night with `restic`.
- **Configuration:** already in the infrastructure repository; the `.env` is kept there encrypted.

## Where (the 3-2-1 rule)

Three copies, on two different media, one of them offline:

1. The original, on the server.
2. Object storage at another provider.
3. A weekly external drive, kept disconnected.

The server's RAID 1 protects against a disk failure, but not against accidental deletion: it doesn't replace any of these copies.

## Objectives

At most 24 hours of data lost, and recovery within 4 hours.
<!-- @chatgpt-answer -->
**Proposed policy (one-page summary)**

| Backup | Frequency | Retention | Where |
| --- | --- | --- | --- |
| Daily | Every night | 14 days | Cloud (another provider) |
| Weekly | Sunday | 8 weeks | Cloud and external drive |
| Monthly | First day of the month | The latest ones | Cloud |

**Checks**

- Automatic alert if a backup fails.
- Monthly restore test on a staging server.
- Annual full recovery drill.

**Responsibilities:** one person owns the backups and checks the alerts every morning.

**Security:** every backup encrypted; the key in the team's password manager.
<!-- @v1 -->
# Backup policy

**Scope:** the `vps-01` server (application, database and user files).

## What is backed up, and how often

| What | How | How often |
| --- | --- | --- |
| Database | Full `pg_basebackup` and transaction log | Nightly (02:00) and every 15 min |
| User files | Incremental backup with `restic` | Nightly |
| Configuration | Infrastructure repository and encrypted `.env` | On every change |

## Where it is kept (the 3-2-1 rule)

1. The original, on the server.
2. An encrypted copy in object storage at another provider.
3. A weekly offline copy, on an external drive kept by the backup owner.

The server's disks are already in RAID 1, which protects it from a single disk failure. Even so, the external copies must be kept, because RAID doesn't protect against accidental deletion or an attack.

## How long

| Backups | Kept for |
| --- | --- |
| Daily | 14 days |
| Weekly | 8 weeks |
| Monthly | The latest ones |

## How it is checked

- **Every day:** an alert if the night's backup didn't finish cleanly.
- **Every month:** the database is restored on a staging server.
- **Every year:** a full drill, rebuilding the whole server from scratch.

## Objectives

- **Maximum data loss (RPO):** 24 hours.
- **Maximum recovery time (RTO):** 4 hours.

## Keys

The `restic` repository password is in the team's password manager and, on paper, in a sealed envelope at the office. Without it, the backups can't be read.
<!-- @v2 -->
# Backup policy

**Scope:** the `vps-01` server (application, database and user files).

## What is backed up, and how often

| What | How | How often |
| --- | --- | --- |
| Database | Full `pg_basebackup` and transaction log | Nightly (02:00) and every 15 min |
| User files | Incremental backup with `restic` | Nightly |
| Configuration | Infrastructure repository and encrypted `.env` | On every change |

## Where it is kept (the 3-2-1 rule)

1. The original, on the server.
2. An encrypted copy in object storage at another provider.
3. A weekly offline copy, on an external drive kept by the backup owner.

## How long

| Backups | Kept for |
| --- | --- |
| Daily | 14 days |
| Weekly | 8 weeks |
| Monthly | 12 months |

## How it is checked

- **Every day:** an alert if the night's backup didn't finish cleanly.
- **Every month:** the database is restored on a staging server.
- **Every year:** a full drill, rebuilding the whole server from scratch.

## Objectives

- **Maximum data loss (RPO):** 15 minutes for the database and 24 hours for files.
- **Maximum recovery time (RTO):** 4 hours.

## Keys

The `restic` repository password is in the team's password manager and, on paper, in a sealed envelope at the office. Without it, the backups can't be read.
<!-- @v3 -->
# Backup policy

**Scope:** the `vps-01` server (application, database and user files).
**Owner:** Emma. **Deputy:** Tom.

## What is backed up, and how often

| What | How | How often |
| --- | --- | --- |
| Database | Full `pg_basebackup` and transaction log | Nightly (02:00) and every 15 min |
| User files | Incremental backup with `restic` | Nightly |
| Configuration | Infrastructure repository and encrypted `.env` | On every change |

## Where it is kept (the 3-2-1 rule)

1. The original, on the server.
2. An encrypted copy in object storage at another provider.
3. A weekly offline copy, on an external drive kept by the backup owner.

## How long

We keep **daily backups for 14 days**, **weekly ones for 8 weeks** and **monthly ones for 12 months**; `restic forget --prune` deletes the rest.

## How it is checked

- **Every day:** an alert in the team chat if the night's backup didn't finish cleanly.
- **On the first Monday of each month:** the owner restores the database on a staging server and checks that the application starts on it. They log the result and how long it took.
- **Every year:** a full drill, rebuilding the whole server from scratch.

## Objectives

- **Maximum data loss (RPO):** 15 minutes for the database and 24 hours for files.
- **Maximum recovery time (RTO):** 4 hours.

## Keys

The `restic` repository password is in the team's password manager and, on paper, in a sealed envelope at the office. Without it, the backups can't be read.
<!-- @changes-1 -->
- [merge] The section structure and the 3-2-1 rule come from Claude's answer.
- [merge] The retention table and the checks come from ChatGPT's answer.
<!-- @review-2-claude -->
- [defect] Objectives: a 24-hour RPO contradicts archiving the transaction log every 15 minutes.
- [simplification] Drop the RAID paragraph: RAID is not a backup.
<!-- @review-2-chatgpt -->
- [defect] Retention: “the latest ones” doesn't say how many monthly backups are kept.
<!-- @changes-2 -->
- [defect] The database's RPO is 15 minutes, in line with the transaction log.
- [defect] Monthly backups are kept for 12 months.
- [simplification] Dropped the RAID paragraph.
<!-- @review-3-claude -->
- [requirement] The brief asks how restores are checked: the monthly test has no owner and no set day.
<!-- @review-3-chatgpt -->
- [simplification] The retention table has three rows: one sentence says the same in less space.
- [clarity] “The database is restored” doesn't say what is checked or where it's logged.
<!-- @changes-3 -->
- [requirement] There's an owner and a deputy, and the monthly test has a set day: the first Monday.
- [clarity] The test says what is checked and where the result is logged.
- [simplification] Retention is now one sentence instead of a table.
<!-- @review-4-claude -->
- [clarity] The daily alert doesn't say who gets it or who acts on it.
