// Published support address used when the build does not provide an override.
export const DEFAULT_CONTACT_ADDRESS = "christophertrogers37@gmail.com";
export function buildContact(value = process.env.CONTACT_ADDRESS) {
  const contact = value || DEFAULT_CONTACT_ADDRESS;
  if (/CONTACT_ADDRESS/i.test(contact) || !contact.trim())
    throw Error("Public contact contains an unresolved placeholder.");
  return contact;
}
