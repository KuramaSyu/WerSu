Garage is the S3 Bucket service. For configuration it uses `infrastructure/.garage.env` and `infrastructure/garage.toml`.
The env file is created with 
```bash
cat > .garage.env <<EOF
GARAGE_DEFAULT_ACCESS_KEY=$(openssl rand -hex 16)
GARAGE_DEFAULT_SECRET_KEY=$(openssl rand -hex 32)
GARAGE_DEFAULT_BUCKET=attachments
EOF
```

THe other things follow the [guide](https://garagehq.deuxfleurs.fr/documentation/quick-start/)