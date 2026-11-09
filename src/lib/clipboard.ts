/**
 * Utility for safely copying text to clipboard with iframe/permission-policy fallback.
 * Prevents unhandled NotAllowedError when Clipboard API is restricted by the document.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (typeof window === 'undefined') return false;

  // 1. Try native navigator.clipboard if allowed
  if (navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Browser permissions policy or non-secure context blocked clipboard.writeText
    }
  }

  // 2. Fallback to hidden textarea with execCommand('copy')
  try {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    textArea.setAttribute('readonly', '');
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    // oxlint-disable-next-line typescript/no-deprecated
    const successful = document.execCommand('copy');
    document.body.removeChild(textArea);
    return successful;
  } catch {
    return false;
  }
}
