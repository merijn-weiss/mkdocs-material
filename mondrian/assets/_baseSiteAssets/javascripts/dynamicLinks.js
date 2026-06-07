/**
 * dynamicLinks.js
 * Automatically rewrites dynamic service links based on the current docs domain
 * and opens them in a new browser tab.
 *
 * Example usage in Markdown:
 *   [Vault](#){data-service="vault"}
 *   [NetBox](#){data-service="netbox"}
 *   [Workflow](#){data-service="workflow"}
 */
document.addEventListener("DOMContentLoaded", function() {
  const host = window.location.hostname; // e.g., docs.deployervm.kreative.green
  const protocol = window.location.protocol; // keep http/https
  const baseDomain = host.replace(/^docs\./, ""); // → deployervm.kreative.green

  // Update all links that define a data-service attribute
  document.querySelectorAll("a[data-service]").forEach(a => {
    const service = a.dataset.service; // e.g. "vault"
    const newUrl = `${protocol}//${service}.${baseDomain}`;
    a.href = newUrl;

    // Open in new tab safely
    a.target = "_blank";
    a.rel = "noopener noreferrer";

    // If the link text is a placeholder, replace it with the actual URL label
    if (a.textContent.trim() === "" || a.textContent.includes("#")) {
      a.textContent = `${service}.${baseDomain}`;
    }
  });
});
