# Missouri Rut Tracker: GitHub Pages site

This repository publishes the password-protected Missouri Rut Tracker (password **Deer**) as an ordinary website. Anyone with the link and the password can open it in a desktop or laptop browser. They don't need an account and don't download anything.

- **index.html** is the dashboard. Its page content and data are encrypted with the password. The code that runs it is not.
- **feed/** holds the GFS/ECMWF model runs, ensembles and Climate Prediction Center outlooks the page reads. A GitHub Action refreshes it 4 times a day.
- **Field reports and roadkill logs** go to a Google Sheet you own, through a small Apps Script web app. Its address is encrypted in `config.enc.json`.

```
index.html                     the dashboard (encrypted with the password)
feed/*.json                    starter copy of the weather feed (the Action replaces it on every run)
config.enc.json                you add this in Part B (Sheet address + key, encrypted)
scripts/rut_updater.py         downloads models, ensembles and CPC outlooks (Python 3 + pyshp)
scripts/rk_model.py            how the roadkill index was fit (reference only)
.github/workflows/pages.yml    refresh + publish, 4 times a day and on every push
apps-script/Code.gs            the Google Sheet web app (paste into Apps Script)
tools/make-config.html         makes config.enc.json; open it from your computer
```

---

## Part A: put the site on GitHub (about 10 minutes)

1. **Create the repository.** Sign in at github.com, click **New repository** and name it something like `rut-tracker`.
   - Make it **Public**. Free GitHub Pages needs a public repository.
   - The page content stays private: without the password it's unreadable, and field reports never go into the repository.
   - Click **Create repository**.
2. **Upload the files.**
   - On the empty repository page, click **uploading an existing file**.
   - Drag in everything from this folder, including the `.github` folder and `.nojekyll`. On a Mac, press Cmd+Shift+. in Finder to show hidden files.
   - Click **Commit changes**.
   - If you use git instead:
     ```
     git init && git add . && git commit -m "Rut tracker" && git branch -M main
     git remote add origin https://github.com/YOUR-NAME/rut-tracker.git && git push -u origin main
     ```
   - Check that `.github/workflows/pages.yml` shows up in the repository. Without it nothing runs.
3. **Turn on Pages.** Go to **Settings > Pages**. Under **Build and deployment > Source**, choose **GitHub Actions**.
4. **Run it once.**
   - Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
   - Pick **Refresh weather and publish**, then **Run workflow**.
   - It takes 2–4 minutes. A green check means it's live.
5. **Open the site** at `https://YOUR-NAME.github.io/rut-tracker/` and enter **Deer**.
   - The Share button in the header copies that link, named "Missouri Rut Tracker" when pasted into email.

**After that, it runs on its own:**
- The Action runs at about 3:40 AM, 9:40 AM, 3:40 PM and 9:40 PM Central (an hour later during daylight time).
- It pulls fresh GFS and ECMWF runs, ensembles and Climate Prediction Center outlooks, then republishes the site.
- Pages that are open re-check the feed every 10 minutes and redraw when it changes.
- The Actions log for each run lists what loaded and any source that failed. A failed source keeps its last good copy.

**Good to know:**
- GitHub sometimes starts scheduled runs 5–30 minutes late.
- GitHub pauses schedules in a public repository after 60 days with no commits. To restart, re-enable the workflow on the Actions tab or make any commit.
- Model data comes from Open-Meteo's free API (non-commercial use) and NOAA CPC.

## Part B: Google Sheet for field reports (about 10 minutes)

Until you finish this part, the site still works, but field reports and roadkill logs stay in each visitor's own browser.

1. **Create the Sheet.**
   - At sheets.google.com, create a blank spreadsheet named something like **Missouri Rut Tracker reports**.
   - It stays private to you. The web app reads and writes it on your behalf, so visitors never get access to the Sheet itself.
2. **Add the script.**
   - In the Sheet, open **Extensions > Apps Script**.
   - Delete what's in `Code.gs`, paste in all of `apps-script/Code.gs` from this folder, and click **Save**.
3. **Run setup once.**
   - In the function menu at the top, pick **setup** and click **Run**.
   - Google asks for permission. Choose your account, then **Advanced > Go to (project name) (unsafe)**, then **Allow**. The "unsafe" warning appears because it's your own unpublished script.
   - The **Execution log** at the bottom prints `KEY: ...`. Copy that key.
   - Back in the Sheet you now have two tabs, **reports** and **roadkill**, with header rows.
4. **Deploy it as a web app.**
   - Click **Deploy > New deployment**. Click the gear next to "Select type" and choose **Web app**.
   - Set **Execute as: Me** and **Who has access: Anyone**, then click **Deploy**.
   - Copy the **Web app URL**. It starts with `https://script.google.com/macros/s/` and ends with `/exec`.
   - "Anyone" only means the page can reach the script without a Google sign-in. The script refuses every request that lacks your key, and only people with the password can get the key.
5. **Make the config file.**
   - On your computer, double-click `tools/make-config.html` to open it in your browser.
   - Enter the password (**Deer**), the Web app URL and the KEY, then click **Make file**.
   - It downloads `config.enc.json`. Nothing is sent anywhere.
6. **Add it to the repository.**
   - In GitHub, click **Add file > Upload files**, drop `config.enc.json` into the top folder and commit.
   - The Action redeploys on its own within a couple of minutes.
7. **Test it.**
   - Open the site, log a field report and a roadkill deer, and confirm both appear as new rows in the Sheet.
   - Then open the site in a second browser or on another computer. The entries should appear there within a minute.

### What the Sheet holds

| Tab | Columns |
|---|---|
| reports | id, date, county, fips, division, stage, count, sex, time, source, notes, entered, owner, received |
| roadkill | id, date, county, fips, n, kind, sex, notes, entered, owner, received |

- **Dates and FIPS codes** are stored as text, so Sheets won't reformat them.
- **`owner`** is a scrambled ID for the browser that sent the row. Each browser can change or delete only its own rows.
- **`received`** is when the row first arrived.
- **Moderating:** delete or fix any row right in the Sheet. Every open page picks up the change within about a minute.
- **Analysis:** filter or pivot on `county`, `division`, `stage` or `date`, or build charts in a separate tab. Leave the **reports** and **roadkill** tabs' column order alone.
- **Changing the script later:** use **Deploy > Manage deployments > Edit (pencil) > Version: New version > Deploy**. That keeps the same URL. A brand-new deployment gives a new URL and needs a new `config.enc.json`.

## Changing the password

The password is built into `index.html` and `config.enc.json`. To change it, have Claude rebuild `index.html` with the new password (the unencrypted source is kept out of this public repository on purpose), then remake `config.enc.json` in `tools/make-config.html`. Visitors who ticked "remember" will be asked for the new one.

## Updates to the dashboard

Replace `index.html` with a new build and commit. The Action republishes on every push to `main`.
