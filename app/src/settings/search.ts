/** Whether a settings row survives the current search.
 *
 * A row carries its label, its help sentence, and any keywords a person might type
 * that are not on screen: the config key, an old name for the thing. Matching all
 * three is what makes searching for `verify_poll_seconds` find "Check for findings
 * to judge".
 */
export function visible(query: string, ...text: (string | undefined)[]): boolean {
  const wanted = query.trim().toLowerCase();
  if (wanted === "") return true;
  return text.some((one) => one !== undefined && one.toLowerCase().includes(wanted));
}
