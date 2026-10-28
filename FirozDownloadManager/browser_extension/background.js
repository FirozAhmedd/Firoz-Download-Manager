const API = "http://127.0.0.1:9614";

async function appRunning() {
  try {
    const r = await fetch(API + "/ping", { cache: "no-store" });
    return r.ok;
  } catch (e) {
    return false;
  }
}

async function send(url, referrer) {
  let cookie = "";
  try {
    const cs = await chrome.cookies.getAll({ url });
    cookie = cs.map(c => c.name + "=" + c.value).join("; ");
  } catch (e) {}
  await fetch(API + "/add", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FDM": "1" },
    body: JSON.stringify({ url, referrer: referrer || "", cookie })
  });
}

// Capture normal browser downloads (only when the program is running)
chrome.downloads.onCreated.addListener(async (item) => {
  const url = item.finalUrl || item.url;
  if (!/^https?:/i.test(url)) return;
  if (!(await appRunning())) return;       // program closed -> browser downloads as usual
  chrome.downloads.cancel(item.id, () => chrome.downloads.erase({ id: item.id }));
  send(url, item.referrer);
});

// Right-click a link -> "Download with Firoz Download Manager"
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "fdm-link",
    title: "Download with Firoz Download Manager",
    contexts: ["link"]
  });
});
chrome.contextMenus.onClicked.addListener(async (info) => {
  if (info.menuItemId !== "fdm-link" || !info.linkUrl) return;
  if (await appRunning()) send(info.linkUrl, info.pageUrl);
});
