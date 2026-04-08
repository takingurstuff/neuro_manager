# How to get a google service account

Neuro manager requires a service account to download files as google does not currently allow for reliable downloads when only authenticated via API Key

## What is a Service Account?

A service account is a special account that behaves exactly like a normal google account, except that google allows bots to use this identity to access content, it makes it easier to manage what bots in a project have access to with standard sharing constraints, it is conceptually similar to a discord bot account

---

## Step 1: Create a New Project

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Log in with your Google Account.
3. Click the **Project Dropdown** at the top left (next to the "Google Cloud" logo).
4. Click **New Project** in the top right of the popup.
5. Enter a name (e.g., "My Drive Project") and click **Create**.
6. Wait a moment for the notification, then click **Select Project**.

---

## Step 2: Enable the Google Drive API

Before the service account can interact with Drive, the API must be turned on for this project.

1. In the left-hand sidebar, go to **APIs & Services** > **Library**.
2. Search for **"Google Drive API"**.
3. Click on the result and click the blue **Enable** button.

---

## Step 3: Create the Service Account

1. Open the sidebar and go to **IAM & Admin** > **Service Accounts**.
2. Click **+ Create Service Account** at the top.
3. **Details:** Enter a name (e.g., "drive-reader"). The ID will generate automatically. Click **Create and Continue**.
4. **Grant Access:** Since `neuro_manager` only needs to see and download from drive, **leave this section empty** ([reason](#why-leave-roles-empty)) and click **Continue**.
5. **User Access:** Leave this empty as well and click **Done**.

---

## Step 4: Generate the JSON Key

This is the "ID card" `neuro_manager` will use to log in as that service account.

1. You should now see your new service account in the list. Click on its **Email address**.
2. Click the **Keys** tab at the top.
3. Click **Add Key** > **Create new key**.
4. Select **JSON** and click **Create**.
5. A `.json` file will automatically download to your computer.
6. **Either** move this file to inside your (planned) library folder and rename it `creds.json`, **or** manually tell `neuro_manager` where is this file with the `--service-account` (or its shorthand `-s`) flag

> **Important:** Keep this file safe and **never** upload it to public sites like GitHub. It contains private keys that grant access to your project.

---

## Step 5: Granting Access to Files (optional, if you ever want to do something more with this account in the future)

Because you did not assign a global "Role" in Step 3, the service account currently has no permission to see anything in your Drive.

Sharing with a service account is straightfoward, it is treated like a normal google account by google drive, you can use the normal share feature, inputting the service account's email address (also known as `client_email`)

### How to find the client email:

There are two methods, the Google Cloud Console, Or the json file you just downloaded

- **Google Drive Method:** Return to **IAM & Admin** > **Service Accounts**, you should see its email right there
- **JSON File Method:** in the JSON file you just downloaded, there should be a key called `client_email`, its value is the service account's email

_But because Neuro Karaoke archive is a public folder, you do not need to explicitly share it with this account for this account to see the folder._

---

## Notes

### Why leave roles empty?

Since Google Service accounts have full access to any API enabled for the project it is in, if the project has some more expensive API like the gemini API, then if a hacker somehow stole that json key file, their bad bots can masquerade as the service account and post requests to gemini, costing the original project owners a lot of money, therefore its considered a security best practice to keep roles and accesses to a minium to reduce risk.
Since `neuro_manager` DOES NOT need any special accesses, we can safely leave roles empty
