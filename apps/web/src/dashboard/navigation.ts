export interface NavigationSeam {
  assign(url: string | URL): void;
}

export const navigation: NavigationSeam = {
  assign(url: string | URL): void {
    window.location.assign(url.toString());
  },
};
