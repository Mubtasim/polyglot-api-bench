FROM mcr.microsoft.com/dotnet/sdk:10.0.403 AS build
WORKDIR /src
COPY dotnet-api.csproj packages.lock.json ./
RUN dotnet restore --locked-mode
COPY *.cs ./
RUN dotnet publish -c Release -o /out --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0.14-noble-chiseled
WORKDIR /app
COPY --from=build /out ./
ENV PORT=8080
EXPOSE 8080
ENTRYPOINT ["dotnet", "dotnet-api.dll"]
