#! /usr/bin/env bash

curl https://ahmia.fyi/address | \
    sed -e '/h3/!d' -e 's/^.*href="//' -e 's/".*//' > urls.txt
