[agent]
backend = <filesystem|s3>
fs_root = <path>
s3_uri = <s3://bucket/prefix>
aws_cli = <path>
work_dir = <path>
staging_dir = <path>
file_slack_seconds = <integer>
max_bytes_per_job = <integer>
min_free_bytes = <integer>
catalog_interval = <integer>
tag_events = <boolean>
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
