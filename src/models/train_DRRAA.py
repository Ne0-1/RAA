from numpy import zeros
import torch
import torch.nn as nn
from scipy.io import mmread
import matplotlib.pyplot as plt
import torch.nn.functional as F
from sklearn import metrics
import networkx as nx 
import seaborn as sns
import numpy as np
from torch_sparse import spspmm

class DRRAA(nn.Module):
    def __init__(self, input_size, k, d, sampling_weights, sample_size, edge_list):
        super(DRRAA, self).__init__()
        self.input_size = input_size
        self.k = k
        self.d = d

        self.beta = torch.nn.Parameter(torch.randn(self.input_size[0]))
        self.A = torch.nn.Parameter(torch.randn(self.d, self.k))
        #self.Z = torch.nn.Parameter(torch.randn(self.k, self.input_size[0]))
        self.Z = torch.nn.Parameter(torch.load("src/models/S_initial.pt"))
        self.G = torch.nn.Parameter(torch.randn(self.input_size[0], self.k))

        self.missing_data = False
        self.sampling_weights = sampling_weights
        self.sample_size = sample_size
        self.sparse_i_idx = edge_list[0]
        self.sparse_j_idx = edge_list[1]


    def sample_network(self):
        # USE torch_sparse lib i.e. : from torch_sparse import spspmm

        # sample for undirected network
        sample_idx = torch.multinomial(self.sampling_weights, self.sample_size, replacement=False)
        # translate sampled indices w.r.t. to the full matrix, it is just a diagonal matrix
        indices_translator = torch.cat([sample_idx.unsqueeze(0), sample_idx.unsqueeze(0)], 0)
        # adjacency matrix in edges format
        edges = torch.cat([self.sparse_i_idx.unsqueeze(0), self.sparse_j_idx.unsqueeze(0)], 0)
        # matrix multiplication B = Adjacency x Indices translator
        # see spspmm function, it give a multiplication between two matrices
        # indexC is the indices where we have non-zero values and valueC the actual values (in this case ones)
        indexC, valueC = spspmm(edges, torch.ones(edges.shape[1]), indices_translator,
                                torch.ones(indices_translator.shape[1]), self.input_size[0], self.input_size[0],
                                self.input_size[0], coalesced=True)
        # second matrix multiplication C = Indices translator x B, indexC returns where we have edges inside the sample
        indexC, valueC = spspmm(indices_translator, torch.ones(indices_translator.shape[1]), indexC, valueC,
                                self.input_size[0], self.input_size[0], self.input_size[0], coalesced=True)

        # edge row position
        sparse_i_sample = indexC[0, :]
        # edge column position
        sparse_j_sample = indexC[1, :]

        return sample_idx, sparse_i_sample, sparse_j_sample

    def log_likelihood(self):
        sample_idx, sparse_sample_i, sparse_sample_j = self.sample_network()

        Z = F.softmax(self.Z, dim=0) #(K x N)
        G = F.sigmoid(self.G) #Sigmoid activation function
        C = (Z.T * G) / (Z.T * G).sum(0) #Gating function

        #For the nodes without links
        beta = self.beta[sample_idx].unsqueeze(1) + self.beta[sample_idx] #(N x N)
        AZCz = torch.mm(self.A, torch.mm(torch.mm(Z[:,sample_idx], C[sample_idx,:]), Z[:,sample_idx])).T
        mat = torch.exp(beta-((AZCz.unsqueeze(1) - AZCz + 1e-06) ** 2).sum(-1) ** 0.5)
        z_pdist1 = (0.5 * torch.mm(torch.exp(torch.ones(sample_idx.shape[0]).unsqueeze(0)),
                                                          (torch.mm((mat - torch.diag(torch.diagonal(mat))),
                                                                    torch.exp(torch.ones(sample_idx.shape[0])).unsqueeze(-1)))))

        #For the nodes with links
        AZC = torch.mm(self.A, torch.mm(Z,C)) #This could perhaps be a computational issue
        z_pdist2 = (self.beta[sparse_sample_i] + self.beta[sparse_sample_j] - (((( torch.matmul(AZC, Z[:, sparse_sample_i]).T - torch.mm(AZC, Z[:, sparse_sample_j]).T + 1e-06) ** 2).sum(-1))) ** 0.5).sum()

        log_likelihood_sparse = z_pdist2 - z_pdist1


        #AZC = torch.matmul(self.A, torch.matmul(Z C))
        
        #beta = self.beta[sample_idx].unsqueeze(1) + self.beta[sample_idx] #(N x N)
        #M_1 = torch.matmul(AZC, Z[:, sample_idx]).T
        #For nodes without links
        #z_pdist1 = ((M_1.unsqueeze(1) - M_1 + 1e-06)**2).sum(-1)**0.5
        #theta = beta - z_pdist1
        #softplus_theta = F.softplus(theta)
        #nodes with links
        #z_pdist2 = ((torch.matmul(AZC, Z[:, sparse_sample_i]).T - torch.matmul(AZC, Z[:, sparse_sample_j]).T + 1e-06)**2).sum(-1)**0.5
        #beta_links = self.beta[sparse_sample_i].unsqueeze(1) + self.beta[sparse_sample_j]

        #log_Lambda_links = beta_links - z_pdist2
        #LL = 0.5 * log_Lambda_links.sum() - 0.5 * torch.sum(softplus_theta-torch.diag(torch.diagonal(softplus_theta)))
        return log_likelihood_sparse
    

    def link_prediction(self, X_test, idx_i_test, idx_j_test):
        with torch.no_grad():
            Z = F.softmax(self.Z, dim=0)
            G = F.sigmoid(self.G)
            C = (Z.T * G) / (Z.T * G).sum(0) #Gating function

            M_i = torch.matmul(self.A, torch.matmul(torch.matmul(Z, C), Z[:, idx_i_test])).T #Size of test set e.g. K x N
            M_j = torch.matmul(self.A, torch.matmul(torch.matmul(Z, C), Z[:, idx_j_test])).T
            z_pdist_test = ((M_i - M_j + 1e-06)**2).sum(-1)**0.5 # N x N 
            theta = (self.beta[idx_i_test] + self.beta[idx_j_test] - z_pdist_test) # N x N

            #Get the rate -> exp(log_odds) 
            rate = torch.exp(theta) # N

            fpr, tpr, threshold = metrics.roc_curve(target, rate.numpy())


            #Determining AUC score and precision and recall
            auc_score = metrics.roc_auc_score(target, rate.cpu().data.numpy())

            return auc_score, fpr, tpr


if __name__ == "__main__": 
    seed = 1984
    torch.random.manual_seed(seed)

    #A = mmread("data/raw/soc-karate.mtx")
    #A = A.todense()
    ZKC_graph = nx.karate_club_graph()
    N = len(ZKC_graph.nodes())
    #Let's keep track of which nodes represent John A and Mr Hi
    Mr_Hi = 0
    John_A = 33

    #Let's display the labels of which club each member ended up joining
    club_labels = nx.get_node_attributes(ZKC_graph,'club')

    #Get the edge list
    edge_list = np.array(list(map(list,ZKC_graph.edges()))).T
    #edge_list.sort(axis=1)  #TODO: Sort in order to recieve the upper triangular part of the adjacency matrix
    edge_list = torch.from_numpy(edge_list).long()

    #Setting number of archetypes and dimensions of latent space
    k = 2
    d = 2

    link_pred = True

    if link_pred:
        num_samples = 15
        idx_i_test = torch.multinomial(input=torch.arange(0, float(N)), num_samples=num_samples,
                                       replacement=True)
        idx_j_test = torch.tensor(np.zeros(num_samples)).long()
        for i in range(len(idx_i_test)):
            idx_j_test[i] = torch.arange(idx_i_test[i].item(), float(N))[
                torch.multinomial(input=torch.arange(idx_i_test[i].item(), float(N)), num_samples=1,
                                  replacement=True).item()].item()  # Temp solution to sample from upper corner

        test = torch.stack((idx_i_test,idx_j_test))

        #TODO: could be a killer.. maybe do it once and save adjacency list ;)
        def if_edge(a, edge_list):
            a = a.tolist()
            edge_list = edge_list.tolist()
            a = list(zip(a[0], a[1]))
            edge_list = list(zip(edge_list[0], edge_list[1]))
            return [a[i] in edge_list for i in range(len(a))]

        target = if_edge(test, edge_list)

    model = DRRAA(input_size = (N, N),
                    k=k,
                    d=d, 
                    sampling_weights=torch.ones(N), 
                    sample_size=20,
                    edge_list=edge_list)

    optimizer = torch.optim.Adam(params=model.parameters(), lr=0.0001)
    
    losses = []
    iterations = 100000
    for _ in range(iterations):
        loss = - model.log_likelihood() / model.input_size[0]
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        losses.append(loss.item())
        print('Loss at the',_,'iteration:',loss.item())
    
    #Link prediction
    if link_pred:
        auc_score, fpr, tpr = model.link_prediction(target, idx_i_test, idx_j_test)
        plt.plot(fpr, tpr, 'b', label = 'AUC = %0.2f' % auc_score)
        plt.plot([0, 1], [0, 1],'r--', label='random')
        plt.legend(loc = 'lower right')
        plt.xlabel("False positive rate")
        plt.ylabel("True positive rate")
        plt.title("RAA model")
        plt.show()

    #Plotting latent space
    Z = F.softmax(model.Z, dim=0)
    G = F.sigmoid(model.G)
    C = (Z.T * G) / (Z.T * G).sum(0)

    embeddings = torch.matmul(model.A, torch.matmul(torch.matmul(Z, C), Z)).T
    archetypes = torch.matmul(model.A, torch.matmul(Z, C))

    labels = list(club_labels.values())
    idx_hi = [i for i, x in enumerate(labels) if x == "Mr. Hi"]
    idx_of = [i for i, x in enumerate(labels) if x == "Officer"]

    fig, (ax1, ax2) = plt.subplots(1, 2)
    sns.heatmap(Z.detach().numpy(), cmap="YlGnBu", cbar=False, ax=ax1)
    sns.heatmap(C.T.detach().numpy(), cmap="YlGnBu", cbar=False, ax=ax2)

    if embeddings.shape[1] == 3:
        fig = plt.figure()
        ax = fig.add_subplot(projection='3d')
        ax.scatter(embeddings[:,0].detach().numpy()[idx_hi], embeddings[:,1].detach().numpy()[idx_hi], embeddings[:,2].detach().numpy()[idx_hi], c = 'red', label='Mr. Hi' )
        ax.scatter(embeddings[:,0].detach().numpy()[idx_of], embeddings[:,1].detach().numpy()[idx_of], embeddings[:,2][idx_of].detach().numpy(), c = 'blue', label='Officer')
        ax.scatter(archetypes[0,:].detach().numpy(), archetypes[1,:].detach().numpy(), archetypes[2,:].detach().numpy(), marker = '^', c='black')
        ax.text(embeddings[Mr_Hi,0].detach().numpy(), embeddings[Mr_Hi,1].detach().numpy(), embeddings[Mr_Hi,2].detach().numpy(), 'Mr. Hi')
        ax.text(embeddings[John_A, 0].detach().numpy(), embeddings[John_A, 1].detach().numpy(), embeddings[John_A, 2].detach().numpy(),  'Officer')
        ax.set_title(f"Latent space after {iterations} iterations")
        ax.legend()
    else:
        fig, (ax1, ax2) = plt.subplots(1, 2)
        ax1.scatter(embeddings[:,0].detach().numpy()[idx_hi], embeddings[:,1].detach().numpy()[idx_hi], c = 'red', label='Mr. Hi')
        ax1.scatter(embeddings[:,0].detach().numpy()[idx_of], embeddings[:,1].detach().numpy()[idx_of], c = 'blue', label='Officer')
        ax1.scatter(archetypes[0,:].detach().numpy(), archetypes[1,:].detach().numpy(), marker = '^', c = 'black')
        ax1.annotate('Mr. Hi', embeddings[Mr_Hi,:])
        ax1.annotate('Officer', embeddings[John_A, :])
        ax1.legend()
        ax1.set_title(f"Latent space after {iterations} iterations")
        #Plotting learning curve
        ax2.plot(losses)
        ax2.set_title("Loss")
    plt.show()



