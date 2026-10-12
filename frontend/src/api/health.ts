export async function getHealth(): Promise<boolean> {
  const response = await fetch('/health');
  if (!response.ok) throw new Error('Health request failed');
  const data: unknown = await response.json();
  if (
    typeof data !== 'object' ||
    data === null ||
    !('status' in data) ||
    data.status !== 'ok' ||
    !('service' in data) ||
    data.service !== 'knigoskop-api'
  )
    throw new Error('Invalid health response');
  return true;
}
