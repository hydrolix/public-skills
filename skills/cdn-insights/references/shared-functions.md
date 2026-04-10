# cdn-insights — Shared Functions

SQL functions available for use in queries against this bundle's tables.
These are deployed to the Hydrolix cluster as part of the bundle.

## `breadcrumbs`

Breadcrumbs extraction

```sql
(breadcrumbs, mainregex, valuextract) -> nullIf(extract(extract(decodeURLComponent(assumeNotNull(breadcrumbs)), mainregex), valuextract),'')
```

## `city_name`

City Name

```sql
(ip) -> dictGetString (
  'commons_geoip_city_locations_en',
  'city_name',
  commons_geoname_id (assumeNotNull (ip))
)
```

## `country_iso_code`

Country ISO Code

```sql
(ip) -> dictGetString (
  'commons_geoip_city_locations_en',
  'country_iso_code',
  commons_geoname_id (assumeNotNull (ip))
)
```

## `geoname_id`

Geoname ID

```sql
(ip) -> toUInt64 (
  multiIf (
    isIPv4String (ip),
    dictGetUInt32 (
      'commons_geoip_city_blocks_ipv4',
      'geoname_id',
      tuple (
        IPv4StringToNumOrDefault (toString (assumeNotNull (ip)))
      )
    ),
    isIPv6String (ip),
    dictGetUInt32 (
      'commons_geoip_city_blocks_ipv6',
      'geoname_id',
      tuple (
        IPv6StringToNumOrDefault (toString (assumeNotNull (ip)))
      )
    ),
    0
  )
)
```
