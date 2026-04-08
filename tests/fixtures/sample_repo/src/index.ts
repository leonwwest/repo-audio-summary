import { App } from "./components/App";
import { fetchStatus } from "./api/client";

export async function bootstrap() {
  const status = await fetchStatus();
  return App(status);
}
