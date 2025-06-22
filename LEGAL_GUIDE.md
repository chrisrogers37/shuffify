
# 🛡️ Legal Compliance Guide for Shuffify

To comply with the [Spotify Developer Policy](https://developer.spotify.com/policy) and [Terms](https://developer.spotify.com/terms), this document outlines how to add a **Terms of Service** and **Privacy Policy** to Shuffify and ensure users **agree to them before using the app**.

---

## 1. 📝 Terms of Service (TOS)

### ✅ Purpose
A Terms of Service sets rules for users interacting with Shuffify. It protects us from misuse and outlines our responsibilities.

### 📌 Required Sections
- Introduction & Acceptance
- Description of the Service
- Spotify Integration Disclaimer
- Prohibited Uses (e.g. scraping, reverse engineering)
- Limitation of Liability
- Governing Law
- Contact Info

### ⚙️ Tools to Generate
- [Termly.io – Terms of Service Generator](https://termly.io/products/terms-and-conditions-generator/)
- [Free Terms Generator](https://www.termsfeed.com/terms-conditions-generator/)

### 🗂 File to Create
```
/public/terms.html
```

### 🔗 Example Wording
> By using Shuffify, you agree to the Terms of Service and Privacy Policy.

---

## 2. 🔐 Privacy Policy

### ✅ Purpose
This explains how we handle user data, especially data received via the Spotify Web API (OAuth scopes).

### 📌 Required Sections
- Data We Collect
  - Spotify profile, playlists, track data, etc.
- How We Use the Data
  - For reordering playlists only
- Data Storage
  - Session-based / temporary storage
- Third Parties
  - No sale or sharing of user data
- User Rights
  - How to request data deletion or inquire
- Contact Information

### ⚙️ Tools to Generate
- [FreePrivacyPolicy.com](https://www.freeprivacypolicy.com/)
- [Privacy Policy Generator by Termly](https://termly.io/products/privacy-policy-generator/)

### 🗂 File to Create
```
/public/privacy.html
```

---

## 3. ✅ UI Enforcement Before Spotify Login

### ⛔ Problem
Spotify requires that users **explicitly agree** to your TOS and Privacy Policy **before** authenticating.

### ✅ Solution

Update the login page to:

- Show links to both legal docs
- Add a required checkbox for consent
- Prevent login if unchecked

### 💡 Example HTML

```html
<form action="/login">
  <input type="checkbox" id="legal-consent" required />
  <label for="legal-consent">
    I agree to the <a href="/terms" target="_blank">Terms of Service</a> and
    <a href="/privacy" target="_blank">Privacy Policy</a>.
  </label>
  <br />
  <button type="submit">Login with Spotify</button>
</form>
```

---

## 4. 🔄 Post-Setup: Resubmit for Quota Extension

Once these documents are:
- Created and hosted
- Linked on the login page
- Consent is **explicitly required**

➡️ Reapply via [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/).

---

## 📬 Contact for Questions
For any legal or compliance issues, contact:  
📧 `support@shuffify.com` (or your real contact email)
