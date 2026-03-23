#!/usr/bin/env bash

cmd='.venv/bin/python -m mypy --config-file="mypy.ini"'

eval "$cmd"
mypy_ret_code=$?

printf "mypy_ret_code: %s\n" "$mypy_ret_code"

if [ "$mypy_ret_code" != 0 ];
then
  echo "mypy check failed, has fatal or error"
  echo "python version:"
  .venv/bin/python --version
  echo "package version:"
  .venv/bin/pip list
  exit 1
fi

exit "$mypy_ret_code"
