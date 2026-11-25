[agent]
backend = <filesystem|s3>
fs_root = <path>
s3_uri = <s3://bucket/prefix>
aws_cli = <path>
work_dir = <path>
staging_dir = <path>
* Must be inside $SPLUNK_HOME/var/spool/splunk (Splunk's built-in stash_hec input) and on
  the same filesystem as work_dir.
file_slack_seconds = <integer>
max_bytes_per_job = <integer>
* Most bytes of edge archive files (as stored) one agent reads for one job.
min_free_bytes = <integer>
* Free disk space always left on the work / staging file system. Checked before every file
  and every 64 MiB written.
max_staged_bytes = <integer>
* Before staging the next file, wait while more than this many bytes are staged and not yet
  read by the stash_hec input (all jobs together).
staging_stall_seconds = <integer>
* Fail the task if staged data has not shrunk for this long while waiting.
staging_poll_seconds = <integer>
catalog_interval = <integer>
sample_files = <integer>
* Newest files per sourcetype whose first events are sampled with each catalog publish.
  0 turns field sampling off.
sample_lines = <integer>
sample_bytes = <integer>
* S3 backend only: bytes read from the start of each sampled object.
sample_max_values = <integer>
tag_events = <boolean>
heartbeat_interval = <integer>
task_retention_days = <integer>
log_level = <DEBUG|INFO|WARNING|ERROR>

[control]
sh_uri = <uri>
app = <string>
verify_ssl = <boolean>
ca_file = <path>
local_uri = <uri>
credential_realm = <string>
credential_name = <string>
request_timeout = <integer>
