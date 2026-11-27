# Tastebud on Unraid (Docker Compose Manager)

GitHub Actions builds both images on every push to `main` (after the tests pass) and publishes them:

- `ghcr.io/joegibson001/tastebud-app`
- `ghcr.io/joegibson001/tastebud-importer`

Tags: `latest` (main), `sha-<commit>` for every build, and `1.2.3` / `1.2` for `v1.2.3` git tags. The importer is also rebuilt weekly so it carries a recent yt-dlp.

## 1. Install the plugin

Apps (Community Applications) > search **Docker Compose Manager** > Install. A **Compose** section appears at the bottom of the **Docker** tab.

## 2. Create the folders

In the Unraid terminal (or create the shares in the UI):

```sh
mkdir -p /mnt/user/appdata/tastebud /mnt/user/media/tastebud
```

- Make sure the `appdata` share stays on the **cache** drive (Shares > appdata > Primary storage: Cache, Secondary: none or Array with mover *off* for it). The SQLite databases live there.
- Videos can live on the array: create a `media` share (or use any share you like and set `MEDIA_PATH`).

## 3. Create the stack

Docker tab > Compose > **Add New Stack**. In the *Add New Compose Stack* dialog:

| Field | Value |
|---|---|
| Stack Name | `Tastebud` |
| Description | optional, e.g. `Swipe video feed` |
| Compose Source | **Project folder** (the default; the plugin creates the folder) |
| External ENV File Path | leave empty (uses the `.env` in the project folder) |
| Compose File Discovery | leave as it is |
| Override File Management | **Automatic** (the default) |

Click **Create**. Do not use the *Git repository* source: the compose file sits in a subfolder of the repo, and your secrets must not live in git.

Then open the new stack's editor (click the stack, or its gear menu > **Edit Stack**):

- **Compose file**: replace the contents with [`docker-compose.yml`](docker-compose.yml) and save.
- **ENV / .env file**: paste [`.env.example`](.env.example), then fill in the four secrets. Generate each one in the terminal:

  ```sh
  openssl rand -hex 32
  ```

  Use a password you will remember for `ADMIN_PASSWORD`, and a different random value for each of `SESSION_SECRET`, `INGEST_API_KEY` and `IMPORTER_API_KEY`.

## 4. Start it

Click **Compose Up**. The first start pulls the images and creates the databases. Both containers appear in the Docker tab with the Tastebud icon and should show *healthy* after about 30 seconds.

- Quick test on the LAN: temporarily set `COOKIE_SECURE=false`, Compose Up again, then open `http://<unraid-ip>:8080` and log in as `admin`. Set it back to `true` once SWAG is in front.
- Then open **Account > Open admin > Users** and choose what visitors who are not logged in can see (nothing by default), and create other users.

## 5. HTTPS with SWAG (subdomain)

Installing on phones needs HTTPS, and Tastebud expects its own subdomain (e.g. `tastebud.example.com`), not a subfolder.

1. Put the app on SWAG's Docker network. Find its name with `docker network ls` (often `proxynet` or `swag`). In the compose file, uncomment the `proxynet:` block at the bottom (rename it if needed) and change the app's line to `networks: [default, proxynet]`. Compose Up again.
2. Add `tastebud` to SWAG's `SUBDOMAINS` (or use a wildcard certificate) and point DNS at your server.
3. Create `/mnt/user/appdata/swag/nginx/proxy-confs/tastebud.subdomain.conf`:

   ```nginx
   server {
       listen 443 ssl;
       listen [::]:443 ssl;
       server_name tastebud.*;
       include /config/nginx/ssl.conf;
       client_max_body_size 0;

       location / {
           include /config/nginx/proxy.conf;
           include /config/nginx/resolver.conf;
           set $upstream_app tastebud-app;
           set $upstream_port 8080;
           set $upstream_proto http;
           proxy_pass $upstream_proto://$upstream_app:$upstream_port;
           # stream videos straight through
           proxy_buffering off;
       }
   }
   ```

4. Restart SWAG. With `COOKIE_SECURE=true`, open `https://tastebud.example.com`, then "Install app" (Android/desktop Chrome) or Share > "Add to Home Screen" (iPhone).

Once SWAG works you can remove the `ports:` lines from the app service so it is only reachable through the proxy.

## Updating

Docker tab > Compose > **Update Stack** (or **Compose Pull** then **Compose Up**). Pin a version with `TASTEBUD_TAG=1.2.0` if you want updates only when you choose.

### "Update available" never goes away

Unraid stores the digest of each installed image in `/var/lib/docker/unraid-update-status.json`.
Only the Docker tab's own Update button rewrites that entry. After a Compose update the stored digest
stays on the old image, so Unraid keeps reporting an update that is already installed. After updating
the stack, run this once in the Unraid terminal. It records the digests of the images you now have:

```sh
php -r 'require_once "/usr/local/emhttp/plugins/dynamix.docker.manager/include/DockerClient.php";
$u = new DockerUpdate();
foreach (["tastebud-app", "tastebud-importer"] as $n) {
  $img = "ghcr.io/joegibson001/$n:latest";
  $local = $u->inspectLocalVersion($img);
  if ($local) { $u->setUpdateStatus($img, $local); echo "$img $local\n"; }
}'
```

To check the stored values: `jq '."ghcr.io/joegibson001/tastebud-app:latest"' /var/lib/docker/unraid-update-status.json`.
You can put the same command in a User Scripts script and run it after each update.

## Backups

- `appdata/tastebud/app/app.db`: users, ratings, collections, categories, settings. The Appdata Backup plugin can cover `appdata/tastebud`.
- The media share: videos, posters and the metadata JSON (Admin > Maintenance > "Rebuild from media" can recreate the library from these if the database is lost).

## Notes

- Files are written as user/group 1000. Everything works; if you browse the media share over SMB and want to delete files by hand, you may need to adjust permissions (Tools > New Permissions on that share).
- Optional site cookies for yt-dlp: put `cookies.txt` in `appdata/tastebud/importer/`.
- Proxy: set `PROXY_URL` in the env file (for example `socks5://user:pass@192.168.1.20:1080`, or a gluetun or privoxy container on the same Docker network) and Compose Up. All of the importer's internet traffic then goes through it. A firewall inside the container blocks every direct connection, and the container will not start if the proxy settings or firewall cannot be applied. Admin > Import shows "All traffic via proxy ..." when it is active. Details: [importer/README.md](../importer/README.md#proxy).
- The importer API port (8081) is not exposed. Uncomment its `ports:` lines if you want to call it from scripts or iOS Shortcuts.
