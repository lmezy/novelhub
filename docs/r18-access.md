# R18 Access Control

## Source marking

When adding a source (manual source form or YueDu source import), an admin can
mark it as an R18 source. The flag is stored on `sources.is_r18`.

## Book classification

During every sync, a book is classified as R18 when:

- the source is marked R18, or
- the book title/author/description/tags contain R18 markers such as `r18`,
  `18禁`, `18+`, `成人`, `色情`, `情色`, `限制级`, `nsfw`, `h文`, `h向`.

Every synced book receives one admin-only classification tag:

- `r18` for R18 books
- `all-ages` for everything else

The classification is stored on `books.is_r18` so it can be filtered without
relying on tags.

## Visibility

- New users are created with `r18_enabled = false`.
- New users are created with `non_r18_enabled = true`, so they can see
  non-R18 books by default.
- Visibility has two independent switches:
  - `r18_enabled`: controls R18 books
  - `non_r18_enabled`: controls non-R18 books
- Both on: user sees all books. Both off: user sees no books.
- `can_manage_visibility`: admin-granted permission that shows the R18 and
  All-Ages buttons to a normal user. When off, the user cannot see or change
  these switches.
- Only admins can toggle these switches:

```bash
curl -X PUT http://localhost:8088/api/admin/users/<user_id>/visibility \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"r18_enabled": true, "non_r18_enabled": true}'
```

Grant a user the buttons:

```bash
curl -X PUT http://localhost:8088/api/admin/users/<user_id>/visibility \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"can_manage_visibility": true}'
```

A granted user can then manage their own visibility:

```bash
curl -X PUT http://localhost:8088/api/auth/me/visibility \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"r18_enabled": true, "non_r18_enabled": true}'
```

- The `r18` / `all-ages` tags are only returned to admins. Normal users never
  see these classification tags, even when their R18 switch is enabled.

## Migration

```bash
cd backend
alembic upgrade head
```

Docker startup runs migrations automatically. Existing books keep their
current state as all-ages until they are synced again; run `rebuild index` in
Admin after migration so search filters include the new `is_r18` field.
