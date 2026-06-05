Garage is the S3 Bucket service. For configuration it uses `.env` and `infrastructure/garage.toml`.
The env file is a copy of `.env.default` which these parameters changed:

```
# garage config
GARAGE_DEFAULT_ACCESS_KEY=c170cb4c3726fa9f0ca9fca11bed021f
GARAGE_DEFAULT_SECRET_KEY=b21cd517badda12cde455f125d32babd253c2ebefebc48eb91064791fe9e2a9c
GARAGE_DEFAULT_BUCKET=attachments

# garage for docker, localhost when running on machine
S3_ENDPOINT=http://garage:3900
```

THe other things follow the [guide](https://garagehq.deuxfleurs.fr/documentation/quick-start/)
