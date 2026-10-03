<!-- @question -->
Migrating PostgreSQL 14 to 17 with minimal downtime

I have PostgreSQL 14 with 40 GB of data on a VPS (Debian 12, 4 vCPUs, 8 GB of RAM and a 160 GB disk). The application can be down for 10 minutes at most. What plan do you suggest?
<!-- @claude-answer -->
## `pg_upgrade --link`: 3–5 minutes of downtime

With 40 GB and a 10-minute window, the safest option is **`pg_upgrade` with hard links**: it doesn't copy the data, it only rebuilds the system catalogs, and at this size it usually takes less than a minute.

1. **Set up 17 alongside 14.** Install `postgresql-17` and check compatibility with `pg_upgrade --check` (extensions, data types, encoding).
2. **Take a verified backup.** A `pg_basebackup` the night before, and a test restore of it: with `--link`, once you start 17, the 14 cluster can no longer be trusted.
3. **Stop the application and upgrade:**
   ```bash
   sudo pg_dropcluster 17 main --stop   # the empty cluster the package creates
   sudo pg_upgradecluster -m upgrade --link 14 main
   ```
4. **Start it and rebuild the statistics** with `vacuumdb --all --analyze-in-stages`: without statistics, the first queries can be slow.

**Expected downtime:** 3–5 minutes, most of it spent stopping and starting the services.
<!-- @chatgpt-answer -->
**Short answer:** with 10 minutes to spare you have two valid options. I'd go with **logical replication**: downtime drops to seconds and you can roll back at any moment.

| Option | Downtime | Rollback | Risk |
| --- | --- | --- | --- |
| `pg_upgrade --link` | 3–5 min | Backup only | Low |
| Logical replication | < 1 min | Yes, 14 stays up | Medium |
| `pg_dump` + `pg_restore` | 30–60 min | Yes | Low |

**Steps with logical replication**

- Create a 17 cluster on port `5433` and copy the schema across with `pg_dump --schema-only`.
- `CREATE PUBLICATION` on 14 and `CREATE SUBSCRIPTION` on 17; wait for the initial copy to finish.
- **The switchover:** stop the application, sync the sequences (`setval`) and point it at port `5433`.

> Logical replication copies neither sequences nor schema changes (DDL): freeze the application's migrations until the switchover.
<!-- @claude-critique-1 -->
- Logical replication is the choice when downtime must be seconds, but here the limit is 10 minutes: `--link` fits with room to spare and has fewer moving parts.
- It needs twice the disk (another 40 GB: there's room, but only just), and tables without a primary key reject `UPDATE` and `DELETE` once published: each would need checking.
- It gets one thing right that I'd missed: a way back. I'm adding one, with a rehearsal beforehand and a verified backup.
<!-- @claude-revision-1 -->
## `pg_upgrade --link`, rehearsed first

I'm keeping my recommendation: with a 10-minute window, **`pg_upgrade --link`** fits with room to spare and has fewer moving parts than logical replication.

1. **Full rehearsal** on a snapshot of the VPS: measure the real time and catch incompatible extensions with `pg_upgrade --check`.
2. **Verified backup** the night before (`pg_basebackup` and a test restore): it's your way back.
3. **The window:** `pg_dropcluster 17 main --stop` and `pg_upgradecluster -m upgrade --link 14 main`.
4. **Afterwards:** start the application and run `vacuumdb --all --analyze-in-stages`.

**Plan B:** if you ever need downtime of seconds, the logical replication ChatGPT proposes is the way, but first the tables without a primary key need checking.
<!-- @chatgpt-critique-1 -->
- The plan is correct and, for a 10-minute window, simpler than mine.
- It should say plainly that with `--link`, once 17 has started there's no going back to 14: the backup is the only way back.
- It's missing a rehearsal on a copy of the server, to know the real timing before the window.
<!-- @chatgpt-revision-1 -->
**Revised answer:** with 10 minutes to spare, **`pg_upgrade --link`** is enough and simpler; logical replication only pays off if you need downtime of seconds.

| | `pg_upgrade --link` | Logical replication |
| --- | --- | --- |
| Downtime | 3–5 min | < 1 min |
| Extra disk | Almost none | +40 GB |
| Rollback | From the backup | Immediate |
| Risk | Low | Medium (DDL, sequences) |

Before the window, rehearse it on a snapshot of the VPS and keep a verified backup: it's the only way back.
<!-- @claude-critique-2 -->
- No significant errors: ChatGPT's revised answer matches mine, and its table adds what each option costs in disk.
<!-- @chatgpt-critique-2 -->
- No objections: the plan is complete, with the rehearsal, the verified backup and plan B.
<!-- @synthesis -->
## Final plan: `pg_upgrade --link`, rehearsed first

Claude and ChatGPT agree: with a 10-minute window, **`pg_upgrade --link`** is the simplest option and fits with room to spare. Logical replication stays as plan B, in case you ever need downtime of seconds.

**The week before**

1. A snapshot of the VPS and a full rehearsal: measure the real time.
2. `pg_upgrade --check` to catch incompatible extensions or types.
3. A verified backup with `pg_basebackup` the night before, and a test restore.

**The window (about 5 minutes)**

```bash
sudo systemctl stop app
sudo pg_dropcluster 17 main --stop
sudo pg_upgradecluster -m upgrade --link 14 main
sudo systemctl start app
sudo -u postgres vacuumdb --all --analyze-in-stages   # with the application already running
```

**If something fails:** don't start 14 again once 17 has started; restore the backup from step 3.

| | `pg_upgrade --link` | Logical replication (plan B) |
| --- | --- | --- |
| Downtime | 3–5 min | < 1 min |
| Extra disk | Almost none | +40 GB |
| Complexity | Low | Medium |
