const PROD_HOSTS = new Set(["sailratings.com", "www.sailratings.com"]);

export function isProdHost(host: string | null | undefined): boolean {
  if (!host) return false;
  return PROD_HOSTS.has(host.toLowerCase().split(":")[0]);
}
