#!/bin/bash


echo "Initialize NovelHub"


mkdir -p storage/books

mkdir -p storage/covers

mkdir -p storage/logs

mkdir -p storage/backup


docker compose up -d --build


echo "NovelHub started"

