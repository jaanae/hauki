# hauki

[![Build Status](https://dev.azure.com/City-of-Helsinki/hauki/_apis/build/status/City-of-Helsinki.hauki-experimental?branchName=master)](https://dev.azure.com/City-of-Helsinki/hauki/_build/latest?definitionId=21&branchName=master)

Django application and REST API for managing opening hours across all services

## Issue tracking
Jira: https://helsinkisolutionoffice.atlassian.net/projects/HAUKI/issues/?filter=allissues

## Prerequisites

* Docker
* Docker Compose

OR

* PostgreSQL (>= 10)
* Python (>= 3.7)

## Docker Installation

### Development

The easiest way to develop is

```
git clone https://github.com/City-of-Helsinki/hauki.git
cd hauki
```

Copy the development config file example `config_dev.env.example`
to `config_dev.env` (feel free to edit the configuration file if you have any settings you wish to change):
```
cp config_dev.env.example config_dev.env
docker-compose up dev
```

and open your browser to http://127.0.0.1:8000/.

Run tests with 

```
docker-compose run dev test
```

Also, uncomment line https://github.com/City-of-Helsinki/hauki/blob/master/docker-compose.yml#L29 to activate
configuring the dev environment with a local file.

### Production

Correspondingly, production container can be brought up with

```
docker-compose run deploy
```

In production, configuration is done with corresponding environment variables.

## Local installation

### Database

hauki runs on PostgreSQL. Install the server on Debian-based systems with:

```bash
sudo apt install postgresql
```

Then create a database user and the database itself as the `postgres` system user:

```bash
createuser <your username>
createdb -l fi_FI.UTF-8 -E UTF8 -T template0 -O <your username> hauki;'
```

### Development

#### Prerequisites

* [Pyenv](https://github.com/pyenv/pyenv)
* [Pyenv virtualenv](https://github.com/pyenv/pyenv-virtualenv)

Clone the repo:
```
git clone https://github.com/City-of-Helsinki/hauki.git
cd hauki
```

Initiate a virtualenv and install the Python requirements plus development requirements:
```
pyenv virtualenv hauki-env
pyenv local hauki-env
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Copy the development config file example `config_dev.env.example` to `config_dev.env` 
(feel free to edit the configuration file if you have any settings you wish to change):
```
cp config_dev.env.example config_dev.env
```

Run tests:
```
pytest
```

Run migrations:
```
python manage.py migrate
```

Run dev server:
```
python manage.py runserver
```
and open your browser to http://127.0.0.1:8000/.

#### Pre-commit hooks

Before committing files to the repository it's advisable to use the configured pre-commit hooks (see [`.pre-commit-config.yaml`](./.pre-commit-config.yaml)). The CI pipeline will fail if the files are not formatted correctly.
 
pre-commit is included in the requirements-dev.txt. After cloning the repository you should install the requirements and then install the hooks by running:

```
pre-commit install
```

The pre-commit hook runs isort, black and flake8 checks on the staged files before committing. The badly formatted files are not automatically changed, you have to run black and/or isort yourself.

You can at any time run the following command if you would like to check all of the files.

```
pre-commit run --all-files
```

## Importing data

Currently, importing *targets* from Helsinki metropolitan area unit registry (TPREK) is supported. Import all targets from [TPREK API](https://www.hel.fi/palvelukarttaws/restpages/ver4.html) by
```
python manage.py hours_import tprek --targets
```

---

*Opening hours* may be imported for any Finnish libraries from the [kirjastot.fi API](https://api.kirjastot.fi/).

This requires that libraries already exist in the database (imported from TPREK or created by other means), with correct kirkanta ids in the `identifiers` field. The kirjastot.fi importer doesn't currently import any libraries into the database, but you may suggest a PR that imports all libraries as targets, if you wish to import libraries outside the Helsinki area from the kirjastot.fi API.

Import library opening hours from the kirjastot.fi API for all targets that have kirkanta identifiers by
```
python manage.py hours_import kirjastot --openings
```

---

*Organizations* may be imported for the City of Helsinki decision makers from the [Paatos API](http://api.hel.fi/paatos/v1/), or for the TPREK data publishers (including City of Helsinki opening hour publishers) from the [TPREK API](https://www.hel.fi/palvelukarttaws/restpages/ver4.html).

Import all TPREK publisher organizations by
```
python manage.py import_organizations -c tprek http://www.hel.fi/palvelukarttaws/rest/v4/department/
```
